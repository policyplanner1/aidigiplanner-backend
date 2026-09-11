"""HeyGen Avatar V provider for lip-synced Photo Avatar reels.

Avatar V is opt-in per look. Before rendering, the provider checks the
configured look with GET /v3/avatars/looks/{look_id}: it must be a completed
Photo Avatar and list ``avatar_v`` in ``supported_api_engines``. Each scene
is then rendered by POST /v3/videos with engine ``avatar_v`` and polled until
completion. The scene visual prompt is used as Avatar V's motion prompt.

When no persisted look id exists, the provider creates a reusable Photo Avatar
from the brand's uploaded portrait and exposes its id for the worker to persist.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.models.enums import CreativeQuality, VoiceoverMode
from app.modules.creatives.domain import ReelScene
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import VideoClipResult, VideoProvider

logger = structlog.get_logger(__name__)

_MAGIC_BYTES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
)


def _sniff_image_content_type(data: bytes) -> str:
    for magic, content_type in _MAGIC_BYTES:
        if data.startswith(magic):
            return content_type
    return "image/png"


def _is_retryable(exc: BaseException) -> bool:
    """429/5xx only -- a 4xx content/validation error (bad image, bad
    voice_id) should surface immediately, not retry into the same failure.
    Every mutating call below carries a stable Idempotency-Key, so retrying
    a 5xx (which may have already been applied server-side) is safe -- HeyGen
    replays the original response for a repeated key instead of double-
    creating the avatar/video."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return isinstance(exc, httpx.TransportError)


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return f"{error.get('code')}: {error.get('message')}"
    return str(body)[:500]


class HeyGenAvatarProvider(VideoProvider):
    backend_name = "heygen"

    def __init__(self, settings: CreativeSettings, *, known_avatar_id: str | None = None):
        if not settings.heygen_api_key:
            raise RuntimeError("HEYGEN_API_KEY is not configured")
        if not settings.heygen_default_voice_id:
            raise RuntimeError("HEYGEN_DEFAULT_VOICE_ID is not configured")
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.heygen_api_base_url,
            headers={"X-Api-Key": settings.heygen_api_key},
            timeout=60.0,
        )
        self._known_avatar_id = known_avatar_id or settings.heygen_default_avatar_id or None
        self.avatar_id: str | None = self._known_avatar_id
        self._avatar_id_cache: dict[str, str] = {}
        self._validated_avatar_ids: set[str] = set()

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, url, **kwargs)
        if response.is_error:
            detail = _error_detail(response)
            logger.warning(
                "heygen_error",
                method=method,
                url=url,
                status=response.status_code,
                detail=detail,
            )
            response.raise_for_status()
        return response

    def _avatar_id_for(self, image_bytes: bytes | None) -> str:
        if self._known_avatar_id:
            return self._known_avatar_id
        if image_bytes is None:
            raise ValueError(
                "HeyGen Photo Avatar generation requires an uploaded avatar image when no "
                "HEYGEN_DEFAULT_AVATAR_ID or BrandProfile.heygen_avatar_id is configured."
            )

        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cached = self._avatar_id_cache.get(image_hash)
        if cached:
            return cached

        content_type = _sniff_image_content_type(image_bytes)
        response = self._request(
            "POST",
            "/v3/avatars",
            headers={"Idempotency-Key": f"brand-avatar-{image_hash}"},
            json={
                "type": "photo",
                "name": f"brand-avatar-{image_hash[:12]}",
                "file": {
                    "type": "base64",
                    "media_type": content_type,
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            },
        )
        body = response.json()
        avatar_id = ((body.get("data") or {}).get("avatar_item") or {}).get("id")
        if not isinstance(avatar_id, str) or not avatar_id:
            raise RuntimeError(f"HeyGen Photo Avatar creation returned no look id: {body!r}")
        self._avatar_id_cache[image_hash] = avatar_id
        self.avatar_id = avatar_id
        return avatar_id

    def _ensure_avatar_v_eligible(self, avatar_id: str) -> None:
        if avatar_id in self._validated_avatar_ids:
            return

        deadline = time.monotonic() + self._settings.heygen_poll_timeout_s
        look: dict[str, Any] = {}
        while True:
            body = self._request("GET", f"/v3/avatars/looks/{avatar_id}").json()
            look = body.get("data") or {}
            status = look.get("status")
            if status == "completed":
                break
            if status == "failed":
                raise RuntimeError(
                    f"HeyGen Photo Avatar look {avatar_id!r} failed training: "
                    f"{look.get('error')}"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"HeyGen Photo Avatar look {avatar_id!r} did not finish training within "
                    f"{self._settings.heygen_poll_timeout_s}s."
                )
            time.sleep(self._settings.heygen_poll_interval_s)

        avatar_type = look.get("avatar_type")
        supported_engines = look.get("supported_api_engines") or []
        if avatar_type != "photo_avatar":
            raise RuntimeError(
                f"HeyGen Avatar V requires a Photo Avatar look; {avatar_id!r} is "
                f"{avatar_type or 'an unknown avatar type'!r}."
            )
        if "avatar_v" not in supported_engines:
            raise RuntimeError(
                f"HeyGen Photo Avatar look {avatar_id!r} is not opted in for Avatar V; "
                f"supported_api_engines={supported_engines!r}."
            )
        self._validated_avatar_ids.add(avatar_id)

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
        # A scene with no spoken line (e.g. a beat the ideation prompt left
        # silent) still needs *something* for HeyGen to lip-sync -- fall
        # back through on_screen_text, then the visual_prompt itself, rather
        # than sending an empty script the API would reject outright.
        script_text = scene.vo_line.strip() or scene.on_screen_text.strip() or scene.visual_prompt
        avatar_image = first_frame_image or (reference_images[0] if reference_images else None)
        avatar_id = self._avatar_id_for(avatar_image)
        self._ensure_avatar_v_eligible(avatar_id)
        # Avatar V is a premium path and 1080p is HeyGen's documented example.
        resolution = "1080p"

        start = time.monotonic()
        create_response = self._request(
            "POST",
            "/v3/videos",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "type": "avatar",
                "avatar_id": avatar_id,
                "script": script_text,
                "voice_id": self._settings.heygen_default_voice_id,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
                # ideate_v1.j2's AVATAR MODE guidance already writes each
                # scene's visual_prompt as camera framing/expression/gesture
                # direction for the avatar -- feed it straight to HeyGen's
                # own motion control rather than duplicating that prompt.
                "motion_prompt": scene.visual_prompt,
                "engine": {"type": "avatar_v"},
            },
        ).json()
        video_id = (create_response.get("data") or {}).get("video_id")
        if not video_id:
            raise RuntimeError(f"HeyGen video creation returned no video_id: {create_response!r}")

        logger.info(
            "heygen_request",
            video_id=video_id,
            avatar_id=avatar_id,
            script_preview=script_text[:120],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
        )

        deadline = start + self._settings.heygen_poll_timeout_s
        status_data: dict[str, object] = {}
        while True:
            status_response = self._request("GET", f"/v3/videos/{video_id}").json()
            status_data = status_response.get("data") or {}
            status = status_data.get("status")
            if status == "completed":
                break
            if status == "failed":
                failure = status_data.get("failure_message") or status_data.get("error")
                raise RuntimeError(f"HeyGen video {video_id} failed: {failure}")
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"HeyGen video {video_id} polling timed out after "
                    f"{self._settings.heygen_poll_timeout_s}s"
                )
            time.sleep(self._settings.heygen_poll_interval_s)

        video_url = status_data.get("video_url")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError(
                f"HeyGen video {video_id} completed with no video_url: {status_data!r}"
            )

        video_bytes = self._request("GET", video_url).content
        elapsed = time.monotonic() - start
        raw_duration = status_data.get("duration")
        if isinstance(raw_duration, int | float):
            duration_s = float(raw_duration)
        else:
            duration_s = float(scene.duration_s)
        logger.info(
            "heygen_response",
            video_id=video_id,
            elapsed_s=round(elapsed, 2),
            bytes=len(video_bytes),
        )

        return VideoClipResult(
            video_bytes=video_bytes,
            video_uri=video_url,
            model_id="heygen-avatar-v-v3",
            duration_s=duration_s,
            estimated_cost_inr=self._settings.costs.estimate_video_call_inr(
                "heygen", int(round(duration_s))
            ),
        )
