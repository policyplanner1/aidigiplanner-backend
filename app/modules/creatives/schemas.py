from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import (
    ContentStatus,
    CreativeAssetKind,
    CreativeFormat,
    CreativeLanguage,
    CreativeQuality,
    GenerationJobStatus,
    ReelStyle,
    SocialPlatform,
    VoiceoverMode,
)


class GenerateCreativesRequest(BaseModel):
    product_line: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=3, max_length=300)
    format: CreativeFormat
    carousel_slides: int | None = Field(default=None, ge=3, le=8)
    reel_duration_s: int | None = Field(default=None, ge=4, le=30)
    language: CreativeLanguage = CreativeLanguage.en
    concept_count: int = Field(default=3, ge=1, le=6)
    quality: CreativeQuality = CreativeQuality.standard
    extra_notes: str = ""
    voiceover: VoiceoverMode | None = None
    # story (default) or avatar -- only meaningful when format is "reel".
    # avatar requires the product to have already uploaded a brand-profile
    # avatar image (PUT .../brand-profile/avatar).
    reel_style: ReelStyle | None = None
    # Phase 15's Quick-Create platform checklist.
    platforms: list[SocialPlatform] = Field(default_factory=list)
    sub_product_id: str | None = None
    # Phase 16's "Customize Content" -- all optional, default to the
    # resolved brand profile's own values when unset.
    objective: str = ""
    offer: str = ""
    festival_occasion: str = ""
    audience_override: str | None = None
    tone_override: list[str] | None = None
    cta_override: str | None = None
    reference_image_storage_key: str | None = None
    publishing_date: datetime | None = None
    # Bypasses the idempotency check that would otherwise return an
    # existing job for an identical (product_line, topic, format) brief
    # instead of creating a new one.
    force: bool = False
    # Forces the mock provider even if a real GEMINI_API_KEY is configured,
    # so the pipeline can be exercised at zero cost on demand.
    dry_run: bool = False


class GenerationJobPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    brief_id: str
    product_id: str
    company_id: str
    status: GenerationJobStatus
    prompt_version: str
    dry_run: bool
    estimated_cost_inr: float
    actual_cost_inr: float | None
    error_message: str | None
    requested_by: str
    started_at: UTCDatetime | None
    finished_at: UTCDatetime | None
    created_at: UTCDatetime
    updated_at: UTCDatetime


class CreativeAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    concept_id: str
    kind: CreativeAssetKind
    label: str
    model_id: str
    mime_type: str
    estimated_cost_inr: float
    actual_cost_inr: float | None
    generated_at: UTCDatetime


class CreativeConceptPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    concept_index: int
    angle: str
    hook: str
    caption: str
    hashtags: list[str]
    cta: str
    on_image_headline: str
    on_image_subhead: str
    on_image_kicker: str
    image_prompt: str
    negative_prompt: str
    reel_script: dict[str, Any] | None
    carousel_slides: list[dict[str, Any]] | None
    disclaimer_line: str
    compliance_notes: list[str]
    compliance_rejected: bool
    compliance_rejection_reason: str
    status: ContentStatus
    reviewed_by: str | None
    reviewed_at: UTCDatetime | None
    scheduled_at: UTCDatetime | None
    published_at: UTCDatetime | None
    suggested_posting_time: UTCDatetime | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    # Not populated by model_validate(orm_row) (no ORM relationship exists
    # between CreativeConcept and CreativeAsset -- this codebase uses plain
    # FK columns, not relationship()) -- the router fetches assets
    # separately and assigns this field before returning.
    assets: list[CreativeAssetPublic] = Field(default_factory=list)


class RejectConceptRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ScheduleConceptRequest(BaseModel):
    # Either an explicit time, or use_suggested_time=True to schedule at the
    # concept's own suggested_posting_time (Phase 21's "let AI choose the
    # best time" option) -- see CreativeService.schedule_concept.
    scheduled_at: datetime | None = None
    use_suggested_time: bool = False


class ContentCommentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    concept_id: str
    author_id: str
    body: str
    created_at: UTCDatetime


class AddContentCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
