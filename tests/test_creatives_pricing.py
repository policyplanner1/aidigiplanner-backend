"""Pure-unit tests for pricing.py's ModelRegistry/CostTable/
estimate_brief_cost -- ported from the prototype's tests/test_config.py."""

from typing import Any

import pytest

from app.models.enums import CreativeFormat, CreativeQuality, VoiceoverMode
from app.modules.creatives.domain import Brief, video_backend_for
from app.modules.creatives.pricing import (
    CostTable,
    CreativeSettings,
    ModelRegistry,
    aspect_ratio_for,
    estimate_brief_cost,
)


class TestModelRegistry:
    def test_defaults_are_verified_ids(self) -> None:
        reg = ModelRegistry()
        assert reg.text_fast == "gemini-3.6-flash"
        assert reg.text_quality == "gemini-3.1-pro-preview"
        assert reg.image_standard == "gemini-3.1-flash-image"
        assert reg.image_draft == "gemini-3.1-flash-lite-image"
        assert reg.image_hero == "gemini-3-pro-image"
        assert reg.video_veo == "veo-3.1-generate-preview"
        assert reg.video_omni == "gemini-omni-flash-preview"

    def test_image_model_for_quality(self) -> None:
        reg = ModelRegistry()
        assert reg.image_model_for(CreativeQuality.draft) == reg.image_draft
        assert reg.image_model_for(CreativeQuality.standard) == reg.image_standard
        assert reg.image_model_for(CreativeQuality.hero) == reg.image_hero

    def test_text_model_for_quality(self) -> None:
        reg = ModelRegistry()
        assert reg.text_model_for(CreativeQuality.draft) == reg.text_fast
        assert reg.text_model_for(CreativeQuality.standard) == reg.text_quality
        assert reg.text_model_for(CreativeQuality.hero) == reg.text_quality

    def test_registry_overridable(self) -> None:
        reg = ModelRegistry(text_fast="some-future-model")
        assert reg.text_fast == "some-future-model"


class TestCostTable:
    def test_text_estimate_scales_with_tokens(self) -> None:
        costs = CostTable()
        cheap = costs.estimate_text_call_inr(CreativeQuality.draft, 1000, 500)
        expensive = costs.estimate_text_call_inr(CreativeQuality.draft, 10000, 5000)
        assert expensive > cheap > 0

    def test_quality_tier_is_pricier_than_draft(self) -> None:
        costs = CostTable()
        draft = costs.estimate_text_call_inr(CreativeQuality.draft, 1000, 1000)
        quality = costs.estimate_text_call_inr(CreativeQuality.standard, 1000, 1000)
        assert quality > draft

    def test_image_cost_by_resolution(self) -> None:
        costs = CostTable()
        one_k = costs.estimate_image_call_inr(CreativeQuality.standard, "1K")
        four_k = costs.estimate_image_call_inr(CreativeQuality.standard, "4K")
        assert four_k > one_k > 0

    def test_image_cost_unpriced_resolution_raises(self) -> None:
        costs = CostTable()
        with pytest.raises(ValueError, match="not priced"):
            costs.estimate_image_call_inr(CreativeQuality.draft, "4K")

    def test_video_veo_cost_scales_with_duration(self) -> None:
        costs = CostTable()
        short = costs.estimate_video_call_inr("veo", 4)
        long = costs.estimate_video_call_inr("veo", 8)
        assert long == pytest.approx(short * 2)

    def test_video_4k_pricier_than_720p(self) -> None:
        costs = CostTable()
        p720 = costs.estimate_video_call_inr("veo", 8, "720p")
        p4k = costs.estimate_video_call_inr("veo", 8, "4k")
        assert p4k > p720

    def test_video_omni_cost(self) -> None:
        costs = CostTable()
        assert costs.estimate_video_call_inr("omni", 10) > 0

    def test_unknown_backend_raises(self) -> None:
        costs = CostTable()
        with pytest.raises(ValueError, match="backend"):
            costs.estimate_video_call_inr("unknown", 8)

    def test_reels_more_expensive_than_images(self) -> None:
        """Non-negotiable: reels are by far the most expensive step."""
        costs = CostTable()
        image_cost = costs.estimate_image_call_inr(CreativeQuality.hero, "4K")
        reel_cost = costs.estimate_video_call_inr("veo", 8, "4k")
        assert reel_cost > image_cost


class TestAspectRatioMapping:
    def test_post(self) -> None:
        assert aspect_ratio_for(CreativeFormat.post) == "4:5"

    def test_reel(self) -> None:
        assert aspect_ratio_for(CreativeFormat.reel) == "9:16"


class TestVideoBackendFor:
    def test_native_audio_maps_to_veo(self) -> None:
        assert video_backend_for(VoiceoverMode.native_audio) == "veo"

    def test_silent_text_maps_to_omni(self) -> None:
        assert video_backend_for(VoiceoverMode.silent_text) == "omni"

    def test_none_defaults_to_omni(self) -> None:
        assert video_backend_for(None) == "omni"


class TestEstimateBriefCost:
    def test_post_has_ideation_review_and_image_lines_only(self) -> None:
        settings = CreativeSettings(_env_file=None)
        brief = Brief(product_line="travel", topic="visa cover", format="post")
        estimate = estimate_brief_cost(brief, settings)
        labels = [li.label for li in estimate.line_items]
        assert any("Ideation" in label for label in labels)
        assert any("reviewer" in label for label in labels)
        assert any("Images" in label for label in labels)
        assert not any("video" in label.lower() for label in labels)
        assert estimate.total_inr > 0

    def test_carousel_costs_more_than_post(self) -> None:
        settings = CreativeSettings(_env_file=None)
        post = Brief(product_line="travel", topic="visa cover", format="post")
        carousel = Brief(
            product_line="travel", topic="visa cover", format="carousel", carousel_slides=8
        )
        assert (
            estimate_brief_cost(carousel, settings).total_inr
            > estimate_brief_cost(post, settings).total_inr
        )

    def test_reel_has_video_line_item(self) -> None:
        settings = CreativeSettings(_env_file=None)
        brief = Brief(
            product_line="travel",
            topic="baggage loss",
            format="reel",
            reel_duration_s=16,
            concept_count=1,
        )
        estimate = estimate_brief_cost(brief, settings)
        video_items = [li for li in estimate.line_items if "video" in li.label.lower()]
        assert len(video_items) == 1
        assert video_items[0].quantity == 16  # 1 concept * 16s

    def test_reel_is_the_most_expensive_format(self) -> None:
        """Non-negotiable: reels are by far the most expensive step."""
        settings = CreativeSettings(_env_file=None)
        common: dict[str, Any] = dict(product_line="travel", topic="baggage loss", concept_count=3)
        post = Brief(**common, format="post")
        reel = Brief(**common, format="reel", reel_duration_s=24)
        assert (
            estimate_brief_cost(reel, settings).total_inr
            > estimate_brief_cost(post, settings).total_inr
        )

    def test_native_audio_reel_costs_more_than_silent(self) -> None:
        settings = CreativeSettings(_env_file=None)
        silent = Brief(
            product_line="travel",
            topic="baggage loss",
            format="reel",
            reel_duration_s=16,
            voiceover="silent_text",
        )
        native = Brief(
            product_line="travel",
            topic="baggage loss",
            format="reel",
            reel_duration_s=16,
            voiceover="native_audio",
        )
        assert (
            estimate_brief_cost(native, settings).total_inr
            > estimate_brief_cost(silent, settings).total_inr
        )

    def test_hero_quality_costs_more_than_draft(self) -> None:
        settings = CreativeSettings(_env_file=None)
        draft = Brief(product_line="travel", topic="visa cover", format="post", quality="draft")
        hero = Brief(product_line="travel", topic="visa cover", format="post", quality="hero")
        assert (
            estimate_brief_cost(hero, settings).total_inr
            > estimate_brief_cost(draft, settings).total_inr
        )

    def test_cost_scales_with_concept_count(self) -> None:
        settings = CreativeSettings(_env_file=None)
        one = Brief(product_line="travel", topic="visa cover", format="post", concept_count=1)
        five = Brief(product_line="travel", topic="visa cover", format="post", concept_count=5)
        assert (
            estimate_brief_cost(five, settings).total_inr
            > estimate_brief_cost(one, settings).total_inr
        )


class TestCreativeSettings:
    def test_settings_load_without_env_file(self, monkeypatch: Any, tmp_path: Any) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        s = CreativeSettings(_env_file=None)
        assert s.gemini_api_key == ""
        assert s.max_cost_per_run_inr > 0
        assert s.max_cost_per_day_inr >= s.max_cost_per_run_inr

    def test_settings_reads_api_key_from_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        s = CreativeSettings(_env_file=None)
        assert s.gemini_api_key == "test-key-123"

    def test_settings_has_model_registry_and_costs(self) -> None:
        s = CreativeSettings(_env_file=None)
        assert isinstance(s.models, ModelRegistry)
        assert isinstance(s.costs, CostTable)
