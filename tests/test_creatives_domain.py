"""Pure-unit tests for domain.py's Brief/GeneratedConcept validation --
ported from the prototype's tests/test_models.py."""

import pytest
from pydantic import ValidationError

from app.models.enums import CreativeFormat
from app.modules.creatives.domain import (
    ASPECT_RATIO_BY_FORMAT,
    Angle,
    Brief,
    CarouselSlide,
    GeneratedConcept,
    OnImageText,
    ReelScene,
    ReelScript,
)


def _concept(**overrides: object) -> GeneratedConcept:
    defaults: dict[str, object] = dict(
        angle=Angle.scenario,
        hook="Your flight is cancelled in Frankfurt.",
        caption="What now?\n\nInsurance is the subject matter of solicitation.",
        hashtags=[f"#Tag{i}" for i in range(8)],
        cta="Get a quote in 60 seconds — link in bio",
        on_image_text=OnImageText(headline="Then what?", subhead="Cover that travels with you"),
        image_prompt="A traveller at an airport help desk, editorial style",
        disclaimer_line="Insurance is the subject matter of solicitation.",
    )
    defaults.update(overrides)
    return GeneratedConcept(**defaults)


class TestBriefFormatValidation:
    def test_post_defaults(self) -> None:
        b = Brief(product_line="travel", topic="visa cover", format="post")
        assert b.carousel_slides is None
        assert b.reel_duration_s is None
        assert b.voiceover is None
        assert b.reel_style is None
        assert b.aspect_ratio == "4:5"

    def test_carousel_defaults_slide_count(self) -> None:
        b = Brief(product_line="motor", topic="renewal", format="carousel")
        assert b.carousel_slides == 3

    def test_carousel_slides_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(product_line="motor", topic="renewal", format="carousel", carousel_slides=20)

    def test_carousel_slides_on_post_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(product_line="motor", topic="renewal", format="post", carousel_slides=5)

    def test_reel_defaults(self) -> None:
        b = Brief(product_line="health", topic="waiting periods", format="reel")
        assert b.reel_duration_s == 8
        assert b.voiceover == "silent_text"
        assert b.reel_style == "story"
        assert b.aspect_ratio == "9:16"

    def test_reel_duration_within_range_accepted(self) -> None:
        b = Brief(
            product_line="health", topic="waiting periods", format="reel", reel_duration_s=12
        )
        assert b.reel_duration_s == 12

    def test_reel_duration_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(
                product_line="health", topic="waiting periods", format="reel", reel_duration_s=5
            )

    def test_reel_duration_above_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(
                product_line="health", topic="waiting periods", format="reel", reel_duration_s=31
            )

    def test_reel_duration_at_boundaries_accepted(self) -> None:
        for duration in (8, 30):
            b = Brief(
                product_line="health",
                topic="waiting periods",
                format="reel",
                reel_duration_s=duration,
            )
            assert b.reel_duration_s == duration

    def test_reel_duration_on_post_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(product_line="health", topic="waiting periods", format="post", reel_duration_s=8)

    def test_reel_style_on_post_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Brief(
                product_line="health", topic="waiting periods", format="post", reel_style="avatar"
            )

    def test_avatar_reel_style_accepted(self) -> None:
        b = Brief(
            product_line="health", topic="waiting periods", format="reel", reel_style="avatar"
        )
        assert b.reel_style == "avatar"

    def test_job_key_deterministic(self) -> None:
        b1 = Brief(product_line="travel", topic="Visa Cover", format="post")
        b2 = Brief(product_line="travel", topic="visa cover", format="post")
        assert b1.job_key("v1") == b2.job_key("v1")

    def test_job_key_changes_with_topic(self) -> None:
        b1 = Brief(product_line="travel", topic="visa cover", format="post")
        b2 = Brief(product_line="travel", topic="baggage loss", format="post")
        assert b1.job_key("v1") != b2.job_key("v1")

    def test_job_key_changes_with_reel_style(self) -> None:
        """An avatar reel and a story reel for the same topic are genuinely
        different outputs -- the idempotency key must not collide them."""
        story = Brief(
            product_line="travel", topic="baggage loss", format="reel", reel_style="story"
        )
        avatar = Brief(
            product_line="travel", topic="baggage loss", format="reel", reel_style="avatar"
        )
        assert story.job_key("v1") != avatar.job_key("v1")


class TestAspectRatioMapping:
    def test_all_formats_mapped(self) -> None:
        assert set(CreativeFormat) == set(ASPECT_RATIO_BY_FORMAT)

    def test_post_and_carousel_are_4_5(self) -> None:
        assert ASPECT_RATIO_BY_FORMAT[CreativeFormat.post] == "4:5"
        assert ASPECT_RATIO_BY_FORMAT[CreativeFormat.carousel] == "4:5"

    def test_reel_is_9_16(self) -> None:
        assert ASPECT_RATIO_BY_FORMAT[CreativeFormat.reel] == "9:16"


class TestOnImageText:
    def test_headline_word_limit(self) -> None:
        with pytest.raises(ValidationError):
            OnImageText(headline="one two three four five six seven")

    def test_valid_headline_ok(self) -> None:
        t = OnImageText(headline="Flight cancelled. Then what?")
        assert t.kicker == ""


class TestGeneratedConcept:
    def test_valid_concept_builds(self) -> None:
        c = _concept()
        assert c.rejected is False

    def test_hashtag_count_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _concept(hashtags=["#OnlyOne"])

    def test_hashtag_must_start_with_hash(self) -> None:
        with pytest.raises(ValidationError):
            _concept(hashtags=["NoHash"] + [f"#Tag{i}" for i in range(8)])

    def test_reel_script_total_duration(self) -> None:
        script = ReelScript(
            scenes=[
                ReelScene(visual_prompt="a", duration_s=3),
                ReelScene(visual_prompt="b", duration_s=5),
            ]
        )
        assert script.total_duration_s == 8

    def test_carousel_slides_defaults_to_none(self) -> None:
        assert _concept().carousel_slides is None

    def test_carousel_slides_accepted_when_provided(self) -> None:
        c = _concept(
            carousel_slides=[
                CarouselSlide(visual_prompt="a", headline="One"),
                CarouselSlide(visual_prompt="b", headline="Two"),
            ]
        )
        assert c.carousel_slides is not None
        assert len(c.carousel_slides) == 2


class TestCarouselSlide:
    def test_valid_slide_builds(self) -> None:
        slide = CarouselSlide(visual_prompt="a suitcase on a carousel", headline="Lost bags?")
        assert slide.subhead == ""

    def test_headline_word_limit(self) -> None:
        with pytest.raises(ValidationError):
            CarouselSlide(visual_prompt="x", headline="one two three four five six seven")

    def test_subhead_word_limit(self) -> None:
        with pytest.raises(ValidationError):
            CarouselSlide(
                visual_prompt="x",
                headline="Short",
                subhead="one two three four five six seven eight nine ten eleven twelve thirteen",
            )
