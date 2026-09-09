from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import ImageProvider, LLMProvider, VideoProvider
from app.modules.creatives.providers.gemini_image import GeminiImageProvider
from app.modules.creatives.providers.gemini_text import GeminiTextProvider
from app.modules.creatives.providers.gemini_video import GeminiOmniProvider, GeminiVeoProvider
from app.modules.creatives.providers.heygen_video import HeyGenAvatarProvider
from app.modules.creatives.providers.mock import (
    MockImageProvider,
    MockLLMProvider,
    MockVideoProvider,
)


def get_llm_provider(*, dry_run: bool, settings: CreativeSettings) -> LLMProvider:
    """Mirrors app.modules.email.provider.get_email_service()'s ABC+factory
    pattern, with one difference: the choice can depend on a per-job
    `dry_run` flag (unlike email's static config-only choice), so this
    isn't `lru_cache`'d -- callers pass the flag straight from the request.

    Falls back to the mock provider whenever a real key isn't configured,
    same as the prototype CLI's own --dry-run/no-key fallback -- so the
    whole pipeline can be exercised through Swagger/TestClient at zero API
    cost."""
    if dry_run or not settings.gemini_api_key:
        return MockLLMProvider()
    return GeminiTextProvider(settings)


def get_image_provider(*, dry_run: bool, settings: CreativeSettings) -> ImageProvider:
    if dry_run or not settings.gemini_api_key:
        return MockImageProvider()
    return GeminiImageProvider(settings)


def get_video_provider(
    *,
    dry_run: bool,
    backend: str,
    settings: CreativeSettings,
    heygen_known_avatar_id: str | None = None,
) -> VideoProvider:
    """`backend` ("veo", "omni", or "heygen") comes from
    domain.video_backend_for_reel(voiceover, reel_style) -- native-audio
    story reels need Veo, silent/on-screen-text story reels default to the
    cheaper Omni Flash backend, and avatar-style reels always use HeyGen's
    real lip-synced talking avatar. The mock provider ignores backend
    entirely; it always emits a local ffmpeg lavfi clip regardless.

    heygen gates on its own API key (not gemini_api_key) -- a HeyGen-less
    deployment still falls back to the mock provider for avatar reels.
    `heygen_known_avatar_id` (ignored by every other backend) is threaded
    through from worker.py's BrandProfile.heygen_avatar_id/
    heygen_default_avatar_id resolution -- see HeyGenAvatarProvider's
    module docstring for why reusing an existing avatar_id matters."""
    if backend == "heygen":
        if dry_run or not settings.heygen_api_key:
            return MockVideoProvider()
        return HeyGenAvatarProvider(settings, known_avatar_id=heygen_known_avatar_id)
    if dry_run or not settings.gemini_api_key:
        return MockVideoProvider()
    if backend == "veo":
        return GeminiVeoProvider(settings)
    if backend == "omni":
        return GeminiOmniProvider(settings)
    raise ValueError(f"unknown video backend {backend!r}")
