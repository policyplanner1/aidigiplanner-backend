"""Settings, model registry, and cost table -- ported from the prototype's
app/config.py.

Every Gemini model ID used anywhere in this module comes from ModelRegistry
below. Pipeline/provider code must read model IDs from settings/registry --
never hardcode a model string -- so a model bump is a one-place change.

Model IDs and pricing were verified against the live Gemini API docs by the
prototype on 2026-08-11 (see `creative workflow/NOTES.md` for the full
verification trail and sources); re-verify before relying on this for real
budgeting -- exact INR billing depends on your account's actual rate and
token counts, not this static table.

Kept as its own small pydantic_settings.BaseSettings (CREATIVE_ env prefix)
rather than folded into app.core.config.Settings, which is flat with no
nested sub-settings today -- adding ~15 Gemini-specific fields there would
bloat every other module's config surface.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.enums import CreativeFormat, CreativeQuality
from app.modules.creatives.domain import (
    ASPECT_RATIO_BY_FORMAT,
    REEL_LIKE_FORMATS,
    Brief,
    CostEstimate,
    CostLineItem,
    video_backend_for,
)

_DEFAULT_PROMPTS_DIR = str(Path(__file__).resolve().parent / "prompts")


class ModelRegistry(BaseModel):
    """Model IDs, overridable via env vars (see CreativeSettings) so a model
    bump never requires touching pipeline/provider code."""

    text_fast: str = "gemini-3.6-flash"
    text_quality: str = "gemini-3.1-pro-preview"

    image_draft: str = "gemini-3.1-flash-lite-image"
    image_standard: str = "gemini-3.1-flash-image"
    image_hero: str = "gemini-3-pro-image"

    video_omni: str = "gemini-omni-flash-preview"
    video_veo: str = "veo-3.1-generate-preview"

    def image_model_for(self, quality: CreativeQuality) -> str:
        return {
            CreativeQuality.draft: self.image_draft,
            CreativeQuality.standard: self.image_standard,
            CreativeQuality.hero: self.image_hero,
        }[quality]

    def text_model_for(self, quality: CreativeQuality) -> str:
        return self.text_fast if quality is CreativeQuality.draft else self.text_quality


class ImageCost(BaseModel):
    """INR per generated image, Standard tier, by resolution."""

    per_0_5k: float | None = None
    per_1k: float
    per_2k: float | None = None
    per_4k: float | None = None


class CostTable(BaseModel):
    usd_to_inr: float = 88.0

    text_fast_input_per_1m: float = 1.50 * 88.0
    text_fast_output_per_1m: float = 7.50 * 88.0
    text_quality_input_per_1m: float = 2.00 * 88.0
    text_quality_output_per_1m: float = 12.00 * 88.0

    image_draft: ImageCost = ImageCost(per_1k=0.0336 * 88.0)
    image_standard: ImageCost = ImageCost(
        per_0_5k=0.045 * 88.0, per_1k=0.067 * 88.0, per_2k=0.101 * 88.0, per_4k=0.151 * 88.0
    )
    image_hero: ImageCost = ImageCost(per_1k=0.134 * 88.0, per_2k=0.134 * 88.0, per_4k=0.24 * 88.0)

    video_veo_per_second_720p: float = 0.40 * 88.0
    video_veo_per_second_1080p: float = 0.40 * 88.0
    video_veo_per_second_4k: float = 0.60 * 88.0
    video_omni_per_second: float = 0.10 * 88.0

    def estimate_text_call_inr(
        self, quality: CreativeQuality, est_input_tokens: int, est_output_tokens: int
    ) -> float:
        if quality is CreativeQuality.draft:
            in_rate, out_rate = self.text_fast_input_per_1m, self.text_fast_output_per_1m
        else:
            in_rate, out_rate = self.text_quality_input_per_1m, self.text_quality_output_per_1m
        return (est_input_tokens / 1_000_000) * in_rate + (est_output_tokens / 1_000_000) * out_rate

    def estimate_image_call_inr(self, quality: CreativeQuality, resolution: str) -> float:
        table = {
            CreativeQuality.draft: self.image_draft,
            CreativeQuality.standard: self.image_standard,
            CreativeQuality.hero: self.image_hero,
        }[quality]
        per_res = {
            "0.5K": table.per_0_5k,
            "1K": table.per_1k,
            "2K": table.per_2k,
            "4K": table.per_4k,
        }
        cost = per_res.get(resolution)
        if cost is None:
            raise ValueError(f"resolution {resolution!r} not priced for quality {quality!r}")
        return cost

    def estimate_video_call_inr(
        self, backend: str, duration_s: int, resolution: str = "720p"
    ) -> float:
        if backend == "omni":
            return duration_s * self.video_omni_per_second
        if backend == "veo":
            rate = {
                "720p": self.video_veo_per_second_720p,
                "1080p": self.video_veo_per_second_1080p,
                "4k": self.video_veo_per_second_4k,
            }.get(resolution)
            if rate is None:
                raise ValueError(f"unknown veo resolution {resolution!r}")
            return duration_s * rate
        raise ValueError(f"unknown video backend {backend!r}")


IMAGE_RESOLUTION_BY_QUALITY: dict[CreativeQuality, str] = {
    CreativeQuality.draft: "1K",
    CreativeQuality.standard: "2K",
    CreativeQuality.hero: "2K",
}

VIDEO_RESOLUTION_BY_QUALITY: dict[CreativeQuality, str] = {
    CreativeQuality.draft: "720p",
    CreativeQuality.standard: "720p",
    CreativeQuality.hero: "1080p",
}

# Rough token counts used only for the pre-flight INR estimate shown before
# any paid call fires -- not billing-accurate, just enough to catch a job
# that would blow the cost cap. Actual cost is recorded per-call once real
# providers report usage (see GenerationJob.actual_cost_inr).
IDEATION_INPUT_TOKENS_BASE = 1800
IDEATION_OUTPUT_TOKENS_PER_CONCEPT = 550
COMPLIANCE_REVIEW_INPUT_TOKENS = 300
COMPLIANCE_REVIEW_OUTPUT_TOKENS = 80


def estimate_brief_cost(brief: Brief, settings: CreativeSettings) -> CostEstimate:
    """Pre-flight INR estimate for a Brief, checked against the per-run/
    per-day guardrails before any paid call fires. Estimates the whole
    job's assets (including images/video) even in Sub-phase D, where only
    ideation actually runs yet -- so guardrails correctly reject a brief
    that would blow the budget once image/video rendering lands too."""
    models = settings.models
    costs = settings.costs
    items: list[CostLineItem] = []

    text_model = models.text_model_for(brief.quality)
    ideation_out_tokens = IDEATION_OUTPUT_TOKENS_PER_CONCEPT * brief.concept_count
    ideation_cost = costs.estimate_text_call_inr(
        brief.quality, IDEATION_INPUT_TOKENS_BASE, ideation_out_tokens
    )
    items.append(
        CostLineItem(
            label="Ideation (LLM, all concepts in one call)",
            model_id=text_model,
            quantity=1,
            unit="call",
            unit_cost_inr=ideation_cost,
            subtotal_inr=ideation_cost,
        )
    )

    review_cost_each = costs.estimate_text_call_inr(
        CreativeQuality.draft, COMPLIANCE_REVIEW_INPUT_TOKENS, COMPLIANCE_REVIEW_OUTPUT_TOKENS
    )
    review_total = review_cost_each * brief.concept_count
    items.append(
        CostLineItem(
            label="Compliance reviewer pass",
            model_id=models.text_fast,
            quantity=brief.concept_count,
            unit="concept",
            unit_cost_inr=review_cost_each,
            subtotal_inr=review_total,
        )
    )

    image_model = models.image_model_for(brief.quality)
    resolution = IMAGE_RESOLUTION_BY_QUALITY[brief.quality]
    images_per_concept = brief.carousel_slides if brief.format is CreativeFormat.carousel else 1
    assert images_per_concept is not None
    per_image_cost = costs.estimate_image_call_inr(brief.quality, resolution)
    total_images = images_per_concept * brief.concept_count
    items.append(
        CostLineItem(
            label=f"Images ({resolution}, {images_per_concept}/concept)",
            model_id=image_model,
            quantity=total_images,
            unit="image",
            unit_cost_inr=per_image_cost,
            subtotal_inr=per_image_cost * total_images,
        )
    )

    if brief.format in REEL_LIKE_FORMATS:
        backend = video_backend_for(brief.voiceover)
        video_model = models.video_veo if backend == "veo" else models.video_omni
        resolution_v = VIDEO_RESOLUTION_BY_QUALITY[brief.quality]
        per_second = costs.estimate_video_call_inr(backend, 1, resolution_v)
        duration = brief.reel_duration_s or 0
        total_seconds = duration * brief.concept_count
        items.append(
            CostLineItem(
                label=f"Reel video ({backend}, {resolution_v}, {duration}s/concept)",
                model_id=video_model,
                quantity=total_seconds,
                unit="second",
                unit_cost_inr=per_second,
                subtotal_inr=per_second * total_seconds,
            )
        )

    return CostEstimate(line_items=items)


class CreativeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CREATIVE_", extra="ignore")

    # No CREATIVE_ prefix -- shared verbatim with any other Gemini caller.
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")

    prompts_dir: str = _DEFAULT_PROMPTS_DIR

    # Cost guardrails -- refuse to enqueue a job whose estimate exceeds these.
    max_cost_per_run_inr: float = 500.0
    max_cost_per_day_inr: float = 2000.0

    # Long-running video generation (Veo/Omni) operation polling.
    video_poll_interval_s: float = 10.0
    video_poll_timeout_s: float = 600.0

    models: ModelRegistry = Field(default_factory=ModelRegistry)
    costs: CostTable = Field(default_factory=CostTable)


@lru_cache
def get_creative_settings() -> CreativeSettings:
    return CreativeSettings()


def aspect_ratio_for(format: CreativeFormat) -> str:
    return ASPECT_RATIO_BY_FORMAT[format]
