"""Unit tests for providers/factory.py's get_video_provider -- specifically
the new "heygen" branch (added alongside "veo"/"omni", which have no
existing direct tests of their own; see worker.py's integration tests for
end-to-end coverage of those)."""

import pytest

from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.factory import get_video_provider
from app.modules.creatives.providers.heygen_video import HeyGenAvatarProvider
from app.modules.creatives.providers.mock import MockVideoProvider


class TestGetVideoProviderHeyGenBranch:
    def test_dry_run_returns_mock_even_with_key_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEYGEN_API_KEY", "test-key")
        monkeypatch.setenv("HEYGEN_DEFAULT_VOICE_ID", "voice-123")
        monkeypatch.setenv("HEYGEN_DEFAULT_AVATAR_ID", "digital-twin-123")
        settings = CreativeSettings(_env_file=None)
        provider = get_video_provider(dry_run=True, backend="heygen", settings=settings)
        assert isinstance(provider, MockVideoProvider)

    def test_no_api_key_falls_back_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEYGEN_API_KEY", "")
        settings = CreativeSettings(_env_file=None)
        provider = get_video_provider(dry_run=False, backend="heygen", settings=settings)
        assert isinstance(provider, MockVideoProvider)

    def test_configured_key_returns_real_heygen_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEYGEN_API_KEY", "test-key")
        monkeypatch.setenv("HEYGEN_DEFAULT_VOICE_ID", "voice-123")
        monkeypatch.setenv("HEYGEN_DEFAULT_AVATAR_ID", "digital-twin-123")
        settings = CreativeSettings(_env_file=None)
        provider = get_video_provider(dry_run=False, backend="heygen", settings=settings)
        assert isinstance(provider, HeyGenAvatarProvider)
        assert provider.backend_name == "heygen"

    def test_known_avatar_id_is_threaded_through_to_the_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEYGEN_API_KEY", "test-key")
        monkeypatch.setenv("HEYGEN_DEFAULT_VOICE_ID", "voice-123")
        settings = CreativeSettings(_env_file=None)
        provider = get_video_provider(
            dry_run=False,
            backend="heygen",
            settings=settings,
            heygen_known_avatar_id="persisted_avatar_789",
        )
        assert isinstance(provider, HeyGenAvatarProvider)
        assert provider._known_avatar_id == "persisted_avatar_789"
