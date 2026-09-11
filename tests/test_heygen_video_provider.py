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
def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_key: str = "test-key",
    voice_id: str = "voice-123",
    avatar_id: str = "digital_twin_123",
    poll_timeout_s: float | None = None,
) -> CreativeSettings:
    """CreativeSettings' HeyGen fields are alias-backed env vars (see
    pricing.py), same as gemini_api_key -- set them the same way
    test_creatives_pricing.py does for GEMINI_API_KEY, via monkeypatch.setenv
    + _env_file=None, rather than passing the Python field names as kwargs
    (which pydantic-settings would silently ignore given the alias)."""
    monkeypatch.setenv("HEYGEN_API_KEY", api_key)
    monkeypatch.setenv("HEYGEN_DEFAULT_VOICE_ID", voice_id)
    monkeypatch.setenv("HEYGEN_DEFAULT_AVATAR_ID", avatar_id)
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


def test_sniff_image_content_type() -> None:
    assert _sniff_image_content_type(_PNG_BYTES) == "image/png"
    assert _sniff_image_content_type(b"\xff\xd8\xffpayload") == "image/jpeg"


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

    def __init__(
        self,
        *,
        status_sequence: list[str] | None = None,
        avatar_type: str = "photo_avatar",
        look_status: str = "completed",
        supported_engines: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._status_sequence = status_sequence or ["completed"]
        self._avatar_type = avatar_type
        self._look_status = look_status
        self._supported_engines = supported_engines or ["avatar_iv", "avatar_v"]

    def __call__(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, url))
        if url == "/v3/avatars":
            return httpx.Response(200, json={"data": {"avatar_item": {"id": "photo_123"}}})
        if url.startswith("/v3/avatars/looks/"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": url.rsplit("/", 1)[-1],
                        "avatar_type": self._avatar_type,
                        "status": self._look_status,
                        "supported_api_engines": self._supported_engines,
                    }
                },
            )
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
        assert result.model_id == "heygen-avatar-v-v3"

    def test_avatar_v_eligibility_checked_once_across_scenes(
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

        look_calls = [c for c in dispatcher.calls if c[1].startswith("/v3/avatars/looks/")]
        assert len(look_calls) == 1

    def test_uploaded_image_creates_and_reuses_photo_avatar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch, avatar_id=""))
        dispatcher = _FakeDispatcher()
        provider._request = dispatcher  # type: ignore[method-assign]

        for line in ("First scene.", "Second scene."):
            provider.generate_clip(
                scene=_scene(vo_line=line),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
                first_frame_image=_PNG_BYTES,
            )

        assert provider.avatar_id == "photo_123"
        assert sum(url == "/v3/avatars" for _, url in dispatcher.calls) == 1

    def test_known_avatar_id_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert ("GET", "/v3/avatars/looks/preexisting_avatar_456") in dispatcher.calls

    def test_existing_photo_avatar_does_not_require_reference_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        provider._request = _FakeDispatcher()  # type: ignore[method-assign]

        result = provider.generate_clip(
            scene=_scene(),
            aspect_ratio="9:16",
            quality=CreativeQuality.standard,
            voiceover=VoiceoverMode.native_audio,
        )
        assert result.video_bytes == b"FAKEVIDEOBYTES"

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
                captured.update(kwargs["json"])  # type: ignore[arg-type]
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
        assert captured["engine"] == {"type": "avatar_v"}
        assert captured["resolution"] == "1080p"
        assert "expressiveness" not in captured

    def test_digital_twin_is_rejected_by_photo_avatar_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        dispatcher = _FakeDispatcher(avatar_type="digital_twin")
        provider._request = dispatcher  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="requires a Photo Avatar"):
            provider.generate_clip(
                scene=_scene(),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
            )
        assert not any(url == "/v3/videos" for _, url in dispatcher.calls)

    def test_look_without_avatar_v_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = HeyGenAvatarProvider(_settings(monkeypatch))
        dispatcher = _FakeDispatcher(supported_engines=["avatar_iv"])
        provider._request = dispatcher  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="not opted in for Avatar V"):
            provider.generate_clip(
                scene=_scene(),
                aspect_ratio="9:16",
                quality=CreativeQuality.standard,
                voiceover=VoiceoverMode.native_audio,
            )

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
