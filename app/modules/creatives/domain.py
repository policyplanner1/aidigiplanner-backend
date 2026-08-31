"""Core generation-pipeline data models -- ported from the standalone
`creative workflow` prototype's app/models.py.

Provider-agnostic: nothing here imports from providers/ or pipeline/, so
these can be built by the mock provider, a real Gemini provider, or test
fixtures interchangeably.

Reuses app.models.enums' CreativeFormat/CreativeLanguage/CreativeQuality/
VoiceoverMode (already defined there to back the DB columns) rather than
redefining them, since they're plain StrEnums with no SQLAlchemy coupling.
`Angle` has no DB column (creative_concepts.angle is a free string, since
angle taxonomy may evolve per brand) so it's defined fresh here, only to
give the ideation prompt a fixed set of options to draw from.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    CreativeFormat,
    CreativeLanguage,
    CreativeQuality,
    ReelStyle,
    VoiceoverMode,
)


class Angle(StrEnum):
    myth_bust = "myth-bust"
    deadline = "deadline"
    checklist = "checklist"
    scenario = "scenario"
    number_led = "number-led"
    contrarian = "contrarian"


# Veo 3.1's documented durationSeconds is one of "4"/"6"/"8" only. Longer
# reels are assembled by stitching multiple <=8s scene clips. 4s is Veo's
# actual floor -- a single-scene reel can't render shorter than that. The
# default (unset reel_duration_s) stays at 8s -- unchanged from before this
# floor was lowered, since that's a separate choice from "what's the
# shortest a caller may explicitly request."
REEL_DURATION_MIN_S = 4
REEL_DURATION_DEFAULT_S = 8
REEL_DURATION_MAX_S = 30
VEO_CLIP_DURATION_CHOICES: tuple[int, ...] = (4, 6, 8)

ASPECT_RATIO_BY_FORMAT: dict[CreativeFormat, str] = {
    CreativeFormat.post: "4:5",
    CreativeFormat.carousel: "4:5",
    CreativeFormat.reel: "9:16",
    CreativeFormat.story: "9:16",
    CreativeFormat.video: "9:16",
}

# `video` is mechanically identical to `reel` throughout the pipeline (same
# scene-script generation, same async render path) -- it exists as a
# separate enum member only because Quick-Create's spec lists Reel and Video
# as distinct user-facing choices.
REEL_LIKE_FORMATS: tuple[CreativeFormat, ...] = (CreativeFormat.reel, CreativeFormat.video)

CAROUSEL_SLIDE_RANGE: tuple[int, int] = (3, 8)


class Brief(BaseModel):
    """Everything needed before any paid call fires. `product_line` is a
    free string (not a closed enum, unlike the prototype) -- validated at
    the service layer against the product's BrandProfile.product_lines,
    since product lines are brand-defined rather than fixed to one
    hardcoded insurer's set."""

    product_line: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=3, max_length=300)
    format: CreativeFormat
    carousel_slides: int | None = Field(
        default=None, ge=CAROUSEL_SLIDE_RANGE[0], le=CAROUSEL_SLIDE_RANGE[1]
    )
    reel_duration_s: int | None = None
    language: CreativeLanguage = CreativeLanguage.en
    concept_count: int = Field(default=3, ge=1, le=6)
    quality: CreativeQuality = CreativeQuality.standard
    extra_notes: str = ""
    voiceover: VoiceoverMode | None = None
    # story: scene-by-scene generated b-roll. avatar: every scene keyed off
    # the brand profile's uploaded face instead. Only valid for reels.
    reel_style: ReelStyle | None = None
    # Phase 16's "Customize Content" overrides -- merged onto the resolved
    # BrandProfileDTO by pipeline/ideate.py, not part of job_key() below
    # (they tweak the prompt, not the brief's core identity).
    objective: str = ""
    offer: str = ""
    festival_occasion: str = ""
    audience_override: str | None = None
    tone_override: list[str] | None = None
    cta_override: str | None = None

    @model_validator(mode="after")
    def _check_format_specific_fields(self) -> Self:
        if self.format is CreativeFormat.carousel and self.carousel_slides is None:
            self.carousel_slides = CAROUSEL_SLIDE_RANGE[0]
        if self.format is not CreativeFormat.carousel and self.carousel_slides is not None:
            raise ValueError("carousel_slides is only valid when format is 'carousel'")

        if self.format in REEL_LIKE_FORMATS:
            if self.reel_duration_s is None:
                self.reel_duration_s = REEL_DURATION_DEFAULT_S
            elif not (REEL_DURATION_MIN_S <= self.reel_duration_s <= REEL_DURATION_MAX_S):
                raise ValueError(
                    f"reel_duration_s must be between {REEL_DURATION_MIN_S} and "
                    f"{REEL_DURATION_MAX_S} seconds"
                )
            if self.voiceover is None:
                self.voiceover = VoiceoverMode.silent_text
            if self.reel_style is None:
                self.reel_style = ReelStyle.story
        else:
            if self.reel_duration_s is not None:
                raise ValueError("reel_duration_s is only valid when format is 'reel' or 'video'")
            if self.voiceover is not None:
                raise ValueError("voiceover is only valid when format is 'reel' or 'video'")
            if self.reel_style is not None:
                raise ValueError("reel_style is only valid when format is 'reel' or 'video'")
        return self

    @property
    def aspect_ratio(self) -> str:
        return ASPECT_RATIO_BY_FORMAT[self.format]

    def job_key(self, prompt_version: str) -> str:
        """Idempotency key: identical (product_line, topic, format,
        reel_style, prompt_version) briefs hash the same -- an avatar reel
        and a story reel for the same topic are genuinely different
        outputs, so reel_style must be part of the identity, not just an
        execution detail."""
        raw = "|".join(
            [
                self.product_line,
                self.topic.strip().lower(),
                self.format.value,
                self.reel_style.value if self.reel_style else "",
                prompt_version,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class OnImageText(BaseModel):
    headline: str = Field(max_length=60)
    subhead: str = Field(default="", max_length=90)
    kicker: str = Field(default="", max_length=30)

    @model_validator(mode="after")
    def _check_word_counts(self) -> Self:
        if len(self.headline.split()) > 6:
            raise ValueError("headline must be <= 6 words")
        if self.subhead and len(self.subhead.split()) > 12:
            raise ValueError("subhead must be <= 12 words")
        return self


class CarouselSlide(BaseModel):
    """One slide's own content -- carousels need a real story arc, not the
    same headline/visual repeated on every slide."""

    visual_prompt: str
    headline: str = Field(max_length=60)
    subhead: str = Field(default="", max_length=90)

    @model_validator(mode="after")
    def _check_word_counts(self) -> Self:
        if len(self.headline.split()) > 6:
            raise ValueError("headline must be <= 6 words")
        if self.subhead and len(self.subhead.split()) > 12:
            raise ValueError("subhead must be <= 12 words")
        return self


class ReelScene(BaseModel):
    visual_prompt: str
    vo_line: str = ""
    duration_s: int = Field(ge=1, le=8)
    on_screen_text: str = ""


class ReelScript(BaseModel):
    scenes: list[ReelScene] = Field(min_length=1)

    @property
    def total_duration_s(self) -> int:
        return sum(scene.duration_s for scene in self.scenes)


class GeneratedConcept(BaseModel):
    """One creative concept as produced by the LLM ideation pipeline.

    Named GeneratedConcept (not CreativeConcept) to avoid confusion with
    app.models.creative_concept.CreativeConcept, the persisted SQLAlchemy
    row this gets flattened into once ideation + compliance finish."""

    angle: Angle
    hook: str = Field(max_length=120)
    caption: str = Field(max_length=2200)
    hashtags: list[str] = Field(min_length=8, max_length=15)
    cta: str
    on_image_text: OnImageText
    image_prompt: str
    negative_prompt: str = ""
    reel: ReelScript | None = None
    carousel_slides: list[CarouselSlide] | None = None
    disclaimer_line: str = ""
    compliance_notes: list[str] = Field(default_factory=list)

    # Populated by the compliance gate, not by the LLM.
    rejected: bool = False
    rejection_reason: str = ""

    @model_validator(mode="after")
    def _check_hashtags(self) -> Self:
        for tag in self.hashtags:
            if not re.fullmatch(r"#\w+", tag):
                raise ValueError(f"invalid hashtag: {tag!r}")
        return self


class CostLineItem(BaseModel):
    label: str
    model_id: str
    quantity: float
    unit: str
    unit_cost_inr: float
    subtotal_inr: float


class CostEstimate(BaseModel):
    line_items: list[CostLineItem] = Field(default_factory=list)

    @property
    def total_inr(self) -> float:
        return sum(li.subtotal_inr for li in self.line_items)


def video_backend_for(voiceover: VoiceoverMode | None) -> str:
    """Native audio needs Veo (Omni's video output isn't documented as
    carrying audio); silent on-screen-text reels default to the cheaper
    Omni Flash backend."""
    return "veo" if voiceover is VoiceoverMode.native_audio else "omni"
