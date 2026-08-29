"""Real Gemini video providers -- two backends, two SDK call shapes. Ported
from the prototype's app/providers/gemini_video.py.

- GeminiVeoProvider: the classic long-running-operation API
  (client.models.generate_videos + client.operations.get polling). Raises
  google.genai.errors.ClientError/ServerError on failure -- the ORIGINAL
  error hierarchy, distinct from the Interactions API's compat_errors (see
  gemini_common.py's module docstring for that finding).

- GeminiOmniProvider: the Interactions API (client.interactions.create via
  gemini_common.create_interaction, so it gets the same retry/logging), but
  polled completely differently from Veo: client.files.get(name=...).state,
  not operations.get(...).done.

Veo's reference_images and first-frame image-to-video are mutually
exclusive on a single call -- render_video.py relies on that: scene 1 gets
the reference image (cover, or the brand's avatar) as its literal first
frame, later scenes get it as a style reference instead.
"""

from __future__ import annotations

import time

import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.models.enums import CreativeQuality, VoiceoverMode
from app.modules.creatives.domain import VEO_CLIP_DURATION_CHOICES, ReelScene
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import VideoClipResult, VideoProvider
from app.modules.creatives.providers.gemini_common import create_interaction

logger = structlog.get_logger(__name__)


def _is_retryable_classic(exc: BaseException) -> bool:
    """429/5xx only. client.models.*/client.operations.* raise
    google.genai.errors.ClientError/ServerError -- distinct from the
    Interactions API's compat_errors hierarchy."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return exc.code == 429
    return False


def nearest_veo_clip_duration(requested_s: int) -> int:
    """Veo only accepts 4/6/8s clips -- pick the closest allowed value."""
    return min(VEO_CLIP_DURATION_CHOICES, key=lambda choice: abs(choice - requested_s))


def _extract_file_id(uri: str) -> str:
    """video_output.uri's exact shape isn't documented as a bare
    `files/{id}` resource name -- a naive `uri.split("/")[-1]` can leave a
    `?query` string and/or a `:action` suffix (e.g. `:download`) attached,
    which builds a malformed resource name for files.get(). Strip both
    before taking the last path segment."""
    without_query = uri.split("?", 1)[0]
    last_segment = without_query.rstrip("/").split("/")[-1]
    return last_segment.split(":", 1)[0]


class GeminiVeoProvider(VideoProvider):
    backend_name = "veo"

    def __init__(self, settings: CreativeSettings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    @retry(
        retry=retry_if_exception(_is_retryable_classic),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _start_generation(
        self,
        *,
        model: str,
        source: genai_types.GenerateVideosSource,
        config: genai_types.GenerateVideosConfig,
    ) -> genai_types.GenerateVideosOperation:
        return self._client.models.generate_videos(model=model, source=source, config=config)

    def generate_clip(
        self,
        *,
        scene: ReelScene,
        aspect_ratio: str,
        quality: CreativeQuality,
        voiceover: VoiceoverMode,
        first_frame_image: bytes | None = None,
        reference_images: list[bytes] | None = None,
    ) -> VideoClipResult:
        model_id = self._settings.models.video_veo
        duration = nearest_veo_clip_duration(scene.duration_s)
        resolution = "1080p" if quality is CreativeQuality.hero else "720p"

        image = None
        if first_frame_image is not None:
            image = genai_types.Image(image_bytes=first_frame_image, mime_type="image/png")

        config_kwargs: dict[str, object] = dict(
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration_seconds=duration,
            generate_audio=(voiceover is VoiceoverMode.native_audio),
        )
        if reference_images:
            config_kwargs["reference_images"] = [
                genai_types.VideoGenerationReferenceImage(
                    image=genai_types.Image(image_bytes=ref, mime_type="image/png"),
                    reference_type=genai_types.VideoGenerationReferenceType.STYLE,
                )
                for ref in reference_images
            ]
        config = genai_types.GenerateVideosConfig(**config_kwargs)
        source = genai_types.GenerateVideosSource(prompt=scene.visual_prompt, image=image)

        logger.info(
            "veo_request",
            model=model_id,
            prompt=scene.visual_prompt,
            duration_s=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            has_first_frame=image is not None,
            reference_image_count=len(reference_images) if reference_images else 0,
        )
        start = time.monotonic()
        operation = self._start_generation(model=model_id, source=source, config=config)

        deadline = start + self._settings.video_poll_timeout_s
        while not operation.done:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Veo generation timed out after {self._settings.video_poll_timeout_s}s"
                )
            time.sleep(self._settings.video_poll_interval_s)
            operation = self._client.operations.get(operation)

        elapsed = time.monotonic() - start
        if operation.error:
            logger.warning(
                "veo_error", model=model_id, elapsed_s=round(elapsed, 2), error=str(operation.error)
            )
            raise RuntimeError(f"Veo generation failed: {operation.error}")

        response = operation.response or operation.result
        if not response or not response.generated_videos:
            raise RuntimeError("Veo returned no videos")

        video = response.generated_videos[0].video
        if video is None:
            raise RuntimeError("Veo returned an empty video")
        if video.video_bytes is None:
            self._client.files.download(file=video)
        video_bytes = video.video_bytes
        if video_bytes is None:
            raise RuntimeError("Veo video download produced no bytes")

        logger.info(
            "veo_response", model=model_id, elapsed_s=round(elapsed, 2), bytes=len(video_bytes)
        )
        return VideoClipResult(
            video_bytes=video_bytes,
            video_uri=video.uri,
            model_id=model_id,
            duration_s=float(duration),
            estimated_cost_inr=self._settings.costs.estimate_video_call_inr(
                "veo", duration, resolution
            ),
        )


class GeminiOmniProvider(VideoProvider):
    backend_name = "omni"

    def __init__(self, settings: CreativeSettings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate_clip(
        self,
        *,
        scene: ReelScene,
        aspect_ratio: str,
        quality: CreativeQuality,
        voiceover: VoiceoverMode,
        first_frame_image: bytes | None = None,
        reference_images: list[bytes] | None = None,
    ) -> VideoClipResult:
        model_id = self._settings.models.video_omni
        start = time.monotonic()
        interaction = create_interaction(
            self._client,
            model=model_id,
            input=scene.visual_prompt,
            response_format={"type": "video", "delivery": "uri"},
        )
        video_output = interaction.output_video
        if video_output is None or not video_output.uri:
            raise RuntimeError(f"Omni returned no video for prompt: {scene.visual_prompt[:200]!r}")

        file_name = _extract_file_id(video_output.uri)
        deadline = start + self._settings.video_poll_timeout_s
        while True:
            file_info = self._client.files.get(name=f"files/{file_name}")
            if file_info.state == genai_types.FileState.ACTIVE:
                break
            if file_info.state == genai_types.FileState.FAILED:
                raise RuntimeError(f"Omni video generation failed for {file_name}")
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Omni video polling timed out after {self._settings.video_poll_timeout_s}s"
                )
            time.sleep(self._settings.video_poll_interval_s)

        video_bytes = self._client.files.download(file=video_output.uri)
        elapsed = time.monotonic() - start
        logger.info(
            "omni_video_response",
            model=model_id,
            elapsed_s=round(elapsed, 2),
            bytes=len(video_bytes) if video_bytes else 0,
        )
        return VideoClipResult(
            video_bytes=video_bytes,
            video_uri=video_output.uri,
            model_id=model_id,
            duration_s=float(scene.duration_s),
            estimated_cost_inr=self._settings.costs.estimate_video_call_inr(
                "omni", scene.duration_s
            ),
        )
