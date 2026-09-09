"""Pure-unit tests for HeyGenAvatarProvider (v3 API). No network -- `_request`
(the one method that ever calls httpx) is monkeypatched with a canned
dispatcher, same spirit as test_creatives_mock_providers.py's no-network
mock-provider tests. The real Gemini providers have no equivalent direct
unit tests (see gemini_video.py/gemini_text.py) since they wrap an opaque
SDK client; HeyGen is plain httpx, so this level of coverage is cheap enough
to be worth it."""

from __future__ import annotations

import httpx
import pytest

from app.models.enums import CreativeQuality, VoiceoverMode
from app.modules.creatives.domain import ReelScene
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.heygen_video import (
    HeyGenAvatarProvider,
    _sniff_image_content_type,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 16
_WEBP_BYTES = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_key: str = "test-key",
    voice_id: str = "voice-123",
    poll_timeout_s: float | None = None,
) -> CreativeSettings:
    """CreativeSettings' HeyGen fields are alias-backed env vars (see
    pricing.py), same as gemini_api_key -- set them the same way
    test_creatives_pricing.py does for GEMINI_API_KEY, via monkeypatch.setenv
    + _env_file=None, rather than passing the Python field names as kwargs
    (which pydantic-settings would silently ignore given the alias)."""
    monkeypatch.setenv("HEYGEN_API_KEY", api_key)
    monkeypatch.setenv("HEYGEN_DEFAULT_VOICE_ID", voice_id)
    settings = CreativeSettings(_env_file=None)
    settings.heygen_poll_interval_s = 0.0
    if poll_timeout_s is not None:
        settings.heygen_poll_timeout_s = poll_timeout_s
    return settings


def _scene(**overrides: object) -> ReelScene:
    defaults: dict[str, object] = dict(
        visual_prompt="Medium close-up, confident expression",
        vo_line="Your flight got cancelled -- here's what to do next.",
        duration_s=5,
        on_screen_text="",
    )
    defaults.update(overrides)
    return ReelScene(**defaults)


class TestSniffImageContentType:
    def test_png(self) -> None:
        assert _sniff_image_content_type(_PNG_BYTES) == "image/png"

    def test_jpeg(self) -> None:
        assert _sniff_image_content_type(_JPEG_BYTES) == "image/jpeg"

    def test_webp(self) -> None:
        assert _sniff_image_content_type(_WEBP_BYTES) == "image/webp"

    def test_unknown_defaults_to_png(self) -> None:
        assert _sniff_image_content_type(b"not an image") == "image/png"


class TestConstructorValidation:
    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="HEYGEN_API_KEY"):
            HeyGenAvatarProvider(_settings(monkeypatch, api_key=""))

    def test_missing_voice_id_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="HEYGEN_DEFAULT_VOICE_ID"):
            HeyGenAvatarProvider(_settings(monkeypatch, voice_id=""))


class _FakeDispatcher:
    """Routes the provider's `_request(method, url, **kwargs)` calls to
    canned v3 responses keyed by URL suffix, and counts calls per endpoint
    so tests can assert on caching behaviour. `_request` is called with
    paths relative to the real httpx.Client's base_url (e.g. "/v3/videos"),
    since replacing `_request` entirely bypasses the client that would
    normally resolve them -- the one exception is the final video download,
    which is called with the absolute `video_url` HeyGen returned."""

    def __init__(self, *, status_sequence: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._status_sequence = status_sequence or ["completed"]

    def __call__(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, url))
        if url == "/v3/avatars":
            return httpx.Response(200, json={"data": {"avatar_item": {"id": "avatar_123"}}})
        if url == "/v3/videos":
            return httpx.Response(200, json={"data": {"video_id": "vid_abc", "status": "waiting"}})
        if url == "/v3/videos/vid_abc":
            poll_count = sum(1 for c in self.calls if c[1] == "/v3/videos/vid_abc")
            index = min(poll_count - 1, len(self._status_sequence) - 1)
            status = self._status_sequence[index]
            data: dict[str, object] = {"status": status}
            if status == "completed":
                data["video_url"] = "https://files.example/vid_abc.mp4"
                data["duration"] = 5.0
            elif status == "failed":
                data["error"] = "renderer exploded"
            return httpx.Response(200, json={"data": data})
        if url == "https://files.example/vid_abc.mp4":
            return httpx.Response(200, content=b"FAKEVIDEOBYTES")
        raise AssertionError(f"unexpected URL in test: {url}")


class TestGenerateClip:
    def test_happy_path_returns_video_bytes_and_cost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        dispatcher = _FakeDispatcher()
        provider._request = dispatcher  # type: ignore[method-assign]

        result = provider.generate_clip(
            scene=_scene(),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            first_frame_image=_PNG_BYTES,
        )

        assert result.video_bytes == b"FAKEVIDEOBYTES"
        assert result.video_uri == "https://files.example/vid_abc.mp4"
        assert result.duration_s == 5.0
        assert result.estimated_cost_inr > 0
        assert result.model_id == "heygen-avatar-iv-v3"

    def test_avatar_created_once_and_reused_across_scenes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        dispatcher = _FakeDispatcher()
        provider._request = dispatcher  # type: ignore[method-assign]

        provider.generate_clip(
            scene=_scene(vo_line="Scene one line."),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            reference_images=[_PNG_BYTES],
        )
        provider.generate_clip(
            scene=_scene(vo_line="Scene two line."),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            reference_images=[_PNG_BYTES],
        )

        avatar_calls = [c for c in dispatcher.calls if c[1] == "/v3/avatars"]
        assert len(avatar_calls) == 1

    def test_known_avatar_id_skips_creation_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A persisted BrandProfile.heygen_avatar_id (or the
        HEYGEN_DEFAULT_AVATAR_ID fallback) must never trigger a paid
        POST /v3/avatars call -- see the module docstring on why."""
        provider = HeyGenAvatarProvider(
            _settings(monkeypatch), known_avatar_id="preexisting_avatar_456"
        )
        dispatcher = _FakeDispatcher()
        provider._request = dispatcher  # type: ignore[method-assign]

        result = provider.generate_clip(
            scene=_scene(),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            first_frame_image=_PNG_BYTES,
        )

        assert result.video_bytes == b"FAKEVIDEOBYTES"
        assert provider.avatar_id == "preexisting_avatar_456"
        assert not any(c[1] == "/v3/avatars" for c in dispatcher.calls)

    def test_freshly_created_avatar_id_exposed_for_persistence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """worker.py reads provider.avatar_id after rendering to persist a
        newly-created avatar_id onto BrandProfile -- confirm it's actually
        set to what /v3/avatars returned, not left None."""
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        assert provider.avatar_id is None
        provider._request = _FakeDispatcher()  # type: ignore[method-assign]

        provider.generate_clip(
            scene=_scene(),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            first_frame_image=_PNG_BYTES,
        )

        assert provider.avatar_id == "avatar_123"

    def test_no_reference_image_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        provider._request = _FakeDispatcher()  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="requires an avatar reference image"):
            provider.generate_clip(
                scene=_scene(),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
            )

    def test_empty_vo_line_falls_back_to_on_screen_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        captured: dict[str, object] = {}

        def dispatcher(method: str, url: str, **kwargs: object) -> httpx.Response:
            if url == "/v3/videos":
                captured["script"] = kwargs["json"]["script"]  # type: ignore[index]
            return _FakeDispatcher()(method, url, **kwargs)

        provider._request = dispatcher  # type: ignore[method-assign]
        provider.generate_clip(
            scene=_scene(vo_line="", on_screen_text="Step 1: File your claim"),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            first_frame_image=_PNG_BYTES,
        )
        assert captured["script"] == "Step 1: File your claim"

    def test_motion_prompt_carries_the_scene_visual_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        captured: dict[str, object] = {}

        def dispatcher(method: str, url: str, **kwargs: object) -> httpx.Response:
            if url == "/v3/videos":
                captured["motion_prompt"] = kwargs["json"]["motion_prompt"]  # type: ignore[index]
            return _FakeDispatcher()(method, url, **kwargs)

        provider._request = dispatcher  # type: ignore[method-assign]
        provider.generate_clip(
            scene=_scene(visual_prompt="Close-up, leaning toward camera, open palm gesture"),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
            first_frame_image=_PNG_BYTES,
        )
        assert captured["motion_prompt"] == "Close-up, leaning toward camera, open palm gesture"

    def test_failed_status_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        provider._request = _FakeDispatcher(status_sequence=["failed"])  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="renderer exploded"):
            provider.generate_clip(
                scene=_scene(),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
                first_frame_image=_PNG_BYTES,
            )

    def test_polling_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch, poll_timeout_s=-1.0))
        provider._request = _FakeDispatcher(status_sequence=["processing"])  # type: ignore[method-assign]

        with pytest.raises(TimeoutError):
            provider.generate_clip(
                scene=_scene(),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
                first_frame_image=_PNG_BYTES,
            )
