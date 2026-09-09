"""Real HeyGen video provider -- true lip-synced talking-avatar video,
distinct from Veo/Omni's b-roll-from-a-prompt (gemini_video.py). Used only
for ReelStyle.avatar reels (see domain.video_backend_for_reel).

Targets HeyGen's v3 API, verified 2026-09-08 against developers.heygen.com's
Mintlify-hosted docs (reference/create-video.md, reference/create-avatar.md,
photo-avatar.md, image-to-video.md -- see memory heygen_avatar_video_api.md
for the fetch trail). This superseded an earlier v2 implementation: v3's
schema is a single, internally-consistent discriminated union confirmed
across multiple doc pages, whereas v2/v3 doc fetches previously came back
contradictory. Re-verify against developers.heygen.com if HeyGen's schema
has moved on since.

Single host (`https://api.heygen.com`, no separate upload host like v2 had):
- POST /v3/avatars -- {type: "photo", name, file: {type: "base64",
  media_type, data}} creates a reusable Photo Avatar from the brand's
  uploaded image, returning data.avatar_item.id as a persistent avatar_id.
  Idempotency-Key is set to a hash of the image bytes, so re-running this
  for the same brand image (this job, a later job, even a different worker
  process) replays HeyGen's original response instead of registering a
  duplicate avatar -- HeyGen retains idempotency keys for 24h.
- POST /v3/videos -- {type: "avatar", avatar_id, script, voice_id,
  resolution, aspect_ratio, motion_prompt, expressiveness, engine: {type:
  "avatar_iv"}}. motion_prompt is fed straight from the scene's
  visual_prompt -- ideate_v1.j2's AVATAR MODE guidance already writes that
  as camera framing/expression/gesture direction for the avatar to perform,
  which is exactly what HeyGen's motion_prompt is for. Returns data.video_id.
- GET /v3/videos/{video_id} -- polled until data.status is "completed"
  (-> video_url) or "failed"; "waiting"/"pending"/"processing" keep polling.

Every reel scene shares the same brand-profile avatar image (see worker.py:
render_reel_clips_for_concepts passes the same avatar bytes for every
concept/scene in an avatar-style reel), so the Photo Avatar registration is
cached per provider instance by image hash rather than repeated per scene.

Registering a Photo Avatar costs real money (~$1 as of this writing), so
in-process/idempotency-key caching alone isn't enough -- a brand's avatar_id
should be created once, ever. worker.py passes `known_avatar_id` (from
BrandProfile.heygen_avatar_id, falling back to CreativeSettings.
heygen_default_avatar_id) so an already-registered avatar is reused with no
API call at all; if neither is set, this provider creates one and exposes it
via `self.avatar_id`, which worker.py persists back onto BrandProfile after
rendering succeeds, so the next job skips creation too.
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

# HeyGen's photo avatars get "medium" expressiveness (its default is "low",
# too static for the confident/gesturing framing ideate_v1.j2 already
# writes into every avatar scene's visual_prompt/motion_prompt).
_EXPRESSIVENESS = "medium"

_MAGIC_BYTES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # good enough here -- avatar uploads are pre-validated PNG/JPEG/WEBP
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
        # A caller-supplied avatar_id (from BrandProfile.heygen_avatar_id,
        # or the HEYGEN_DEFAULT_AVATAR_ID fallback) skips avatar creation
        # entirely -- registering a new Photo Avatar costs real money, so
        # once one exists for a brand it should never be recreated.
        self._known_avatar_id = known_avatar_id
        # image sha256 -> HeyGen photo avatar_id, reused across every
        # scene/concept in one job (see module docstring).
        self._avatar_id_cache: dict[str, str] = {}
        # Whichever avatar_id actually ended up being used (known, cached,
        # or freshly created) -- worker.py reads this after rendering to
        # persist a newly-created id back onto BrandProfile.heygen_avatar_id
        # so the next job reuses it instead of paying to create another.
        self.avatar_id: str | None = None

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

    def _avatar_id_for(self, image_bytes: bytes) -> str:
        if self._known_avatar_id:
            self.avatar_id = self._known_avatar_id
            return self._known_avatar_id

        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cached = self._avatar_id_cache.get(image_hash)
        if cached is not None:
            self.avatar_id = cached
            return cached

        content_type = _sniff_image_content_type(image_bytes)
        logger.info("heygen_avatar_create", bytes=len(image_bytes), content_type=content_type)
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
            raise RuntimeError(f"HeyGen avatar creation returned no id: {body!r}")
        self._avatar_id_cache[image_hash] = avatar_id
        self.avatar_id = avatar_id
        return avatar_id

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
        avatar_image = first_frame_image or (reference_images[0] if reference_images else None)
        if avatar_image is None:
            raise ValueError("HeyGen avatar provider requires an avatar reference image")

        # A scene with no spoken line (e.g. a beat the ideation prompt left
        # silent) still needs *something* for HeyGen to lip-sync -- fall
        # back through on_screen_text, then the visual_prompt itself, rather
        # than sending an empty script the API would reject outright.
        script_text = scene.vo_line.strip() or scene.on_screen_text.strip() or scene.visual_prompt
        avatar_id = self._avatar_id_for(avatar_image)
        resolution = "1080p" if quality is CreativeQuality.hero else "720p"

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
                "expressiveness": _EXPRESSIVENESS,
                "engine": {"type": "avatar_iv"},
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
                raise RuntimeError(f"HeyGen video {video_id} failed: {status_data.get('error')}")
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
            model_id="heygen-avatar-iv-v3",
            duration_s=duration_s,
            estimated_cost_inr=self._settings.costs.estimate_video_call_inr(
                "heygen", int(round(duration_s))
            ),
        )
