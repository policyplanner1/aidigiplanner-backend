from datetime import datetime

from sqlalchemy import CHAR, JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import (
    CreativeFormat,
    CreativeLanguage,
    CreativeQuality,
    ReelStyle,
    VoiceoverMode,
)


class CreativeBrief(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "creative_briefs"

    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=False, index=True
    )
    # Free text, not a closed enum -- validated at the service layer against
    # the product's BrandProfile.product_lines instead, since product lines
    # are brand-defined rather than fixed to one hardcoded set.
    product_line: Mapped[str] = mapped_column(String(100), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    format: Mapped[CreativeFormat] = mapped_column(enum_column_type(CreativeFormat), nullable=False)
    carousel_slides: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reel_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[CreativeLanguage] = mapped_column(
        enum_column_type(CreativeLanguage), nullable=False, default=CreativeLanguage.en
    )
    concept_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    quality: Mapped[CreativeQuality] = mapped_column(
        enum_column_type(CreativeQuality), nullable=False, default=CreativeQuality.standard
    )
    extra_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    voiceover: Mapped[VoiceoverMode | None] = mapped_column(
        enum_column_type(VoiceoverMode), nullable=True
    )
    # Only set when format is "reel" -- story (scene-by-scene b-roll) or
    # avatar (every scene keyed off the brand profile's uploaded face).
    reel_style: Mapped[ReelStyle | None] = mapped_column(enum_column_type(ReelStyle), nullable=True)
    # Phase 15's Quick-Create platform checklist -- list[SocialPlatform
    # values]. Purely descriptive today (see the "manual entry" social
    # scope decision) -- nothing publishes to these automatically.
    platforms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sub_product_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("sub_products.id"), nullable=True
    )
    # Phase 16's "Customize Content" optional fields -- all default to the
    # brand profile's own values when unset (see pipeline/ideate.py, which
    # merges non-None overrides onto the resolved BrandProfileDTO).
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    offer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    festival_occasion: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    audience_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone_override: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cta_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference_image_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    publishing_date: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    # sha256[:8] of (product_line, topic, format, prompt_version) -- lets
    # CreativeService.request_generation() find and return an existing job
    # for an identical brief instead of spending money on a duplicate.
    job_key: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
