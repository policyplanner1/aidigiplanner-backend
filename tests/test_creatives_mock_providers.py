"""Pure-unit tests for the mock providers -- ported from the prototype's
tests/test_mock_provider.py. No network, no DB."""

import io

from PIL import Image

from app.models.enums import CreativeQuality, VoiceoverMode
from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import Brief, ReelScene
from app.modules.creatives.providers.mock import (
    MockImageProvider,
    MockLLMProvider,
    MockVideoProvider,
)


class TestMockLLMProvider:
    def test_generates_requested_count(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="travel", topic="visa cover", format="post")
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=3, prompt_version="v1")
        assert len(concepts) == 3

    def test_no_two_concepts_share_an_angle(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="motor", topic="renewal", format="post")
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=4, prompt_version="v1")
        angles = [c.angle for c in concepts]
        assert len(angles) == len(set(angles))

    def test_disclaimer_present_in_every_caption(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="health", topic="waiting periods", format="post")
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=3, prompt_version="v1")
        for c in concepts:
            assert brand.compliance.mandatory_disclaimer in c.caption
            assert c.disclaimer_line == brand.compliance.mandatory_disclaimer

    def test_deterministic_given_same_inputs(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="travel", topic="visa cover", format="post")
        run1 = MockLLMProvider().generate_concepts(brief, brand, count=3, prompt_version="v1")
        run2 = MockLLMProvider().generate_concepts(brief, brand, count=3, prompt_version="v1")
        assert [c.hook for c in run1] == [c.hook for c in run2]
        assert [c.caption for c in run1] == [c.caption for c in run2]

    def test_reel_format_produces_reel_script(self, brand: BrandProfileDTO) -> None:
        brief = Brief(
            product_line="travel",
            topic="baggage loss",
            format="reel",
            reel_duration_s=16,
            voiceover="silent_text",
        )
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=2, prompt_version="v1")
        for c in concepts:
            assert c.reel is not None
            assert len(c.reel.scenes) > 0

    def test_post_format_has_no_reel_script(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="travel", topic="baggage loss", format="post")
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=2, prompt_version="v1")
        assert all(c.reel is None for c in concepts)

    def test_carousel_format_produces_distinct_per_slide_content(
        self, brand: BrandProfileDTO
    ) -> None:
        """Regression coverage: without per-slide content every carousel
        slide repeated the same image, even in dry-run. The mock provider
        must also produce distinct slide content so dry runs actually
        exercise this."""
        brief = Brief(
            product_line="travel", topic="baggage loss", format="carousel", carousel_slides=4
        )
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=1, prompt_version="v1")
        slides = concepts[0].carousel_slides
        assert slides is not None
        assert len(slides) == 4
        headlines = [s.headline for s in slides]
        visual_prompts = [s.visual_prompt for s in slides]
        assert len(set(headlines)) == 4
        assert len(set(visual_prompts)) == 4

    def test_non_carousel_format_has_no_carousel_slides(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="travel", topic="baggage loss", format="post")
        concepts = MockLLMProvider().generate_concepts(brief, brand, count=2, prompt_version="v1")
        assert all(c.carousel_slides is None for c in concepts)

    def test_feedback_is_reflected_in_revised_concept(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="travel", topic="visa cover", format="post")
        concepts = MockLLMProvider().generate_concepts(
            brief, brand, count=2, prompt_version="v1", feedback={0: "used a banned claim"}
        )
        assert "revised after: used a banned claim" in concepts[0].caption

    def test_generate_text_returns_no_network_result(self) -> None:
        result = MockLLMProvider().generate_text("check this caption for violations")
        assert result.model_id == "mock-text"
        assert result.text


class TestMockImageProvider:
    def test_returns_valid_png_bytes(self) -> None:
        result = MockImageProvider().generate_image(
            prompt="a traveller at an airport",
            negative_prompt="",
            aspect_ratio="4:5",
            resolution="1K",
            quality=CreativeQuality.draft,
        )
        img = Image.open(io.BytesIO(result.image_bytes))
        assert img.format == "PNG"

    def test_aspect_ratio_maps_to_correct_dimensions(self) -> None:
        post = MockImageProvider().generate_image(
            prompt="x",
            negative_prompt="",
            aspect_ratio="4:5",
            resolution="1K",
            quality=CreativeQuality.draft,
        )
        reel_cover = MockImageProvider().generate_image(
            prompt="x",
            negative_prompt="",
            aspect_ratio="9:16",
            resolution="1K",
            quality=CreativeQuality.draft,
        )
        post_img = Image.open(io.BytesIO(post.image_bytes))
        reel_img = Image.open(io.BytesIO(reel_cover.image_bytes))
        assert post_img.size[1] > post_img.size[0]  # 4:5 portrait
        assert reel_img.size[1] > reel_img.size[0] * 1.5  # 9:16 taller portrait

    def test_zero_cost(self) -> None:
        result = MockImageProvider().generate_image(
            prompt="x",
            negative_prompt="",
            aspect_ratio="4:5",
            resolution="1K",
            quality=CreativeQuality.draft,
        )
        assert result.estimated_cost_inr == 0.0


class TestMockVideoProvider:
    def test_generates_clip_for_scene(self) -> None:
        scene = ReelScene(visual_prompt="a departure board flickers", duration_s=4)
        result = MockVideoProvider().generate_clip(
            scene=scene,
            aspect_ratio="9:16",
            quality=CreativeQuality.draft,
            voiceover=VoiceoverMode.silent_text,
        )
        assert result.video_bytes is not None
        assert len(result.video_bytes) > 0
        assert result.duration_s == 4.0

    def test_backend_name_is_mock(self) -> None:
        assert MockVideoProvider().backend_name == "mock"
