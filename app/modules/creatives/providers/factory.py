from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import ImageProvider, LLMProvider, VideoProvider
from app.modules.creatives.providers.gemini_image import GeminiImageProvider
from app.modules.creatives.providers.gemini_text import GeminiTextProvider
from app.modules.creatives.providers.gemini_video import GeminiOmniProvider, GeminiVeoProvider
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


def get_video_provider(*, dry_run: bool, backend: str, settings: CreativeSettings) -> VideoProvider:
    """`backend` ("veo" or "omni") comes from domain.video_backend_for(voiceover)
    -- native-audio reels need Veo, silent/on-screen-text reels default to
    the cheaper Omni Flash backend. The mock provider ignores backend
    entirely; it always emits a local ffmpeg lavfi clip regardless."""
    if dry_run or not settings.gemini_api_key:
        return MockVideoProvider()
    if backend == "veo":
        return GeminiVeoProvider(settings)
    if backend == "omni":
        return GeminiOmniProvider(settings)
    raise ValueError(f"unknown video backend {backend!r}")
