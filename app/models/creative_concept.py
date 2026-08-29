from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import ContentStatus


class CreativeConcept(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "creative_concepts"

    job_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("generation_jobs.id"), nullable=False, index=True
    )
    concept_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free text, not a closed enum -- angle taxonomy may evolve per brand.
    angle: Mapped[str] = mapped_column(String(64), nullable=False)
    hook: Mapped[str] = mapped_column(String(120), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cta: Mapped[str] = mapped_column(String(255), nullable=False)
    on_image_headline: Mapped[str] = mapped_column(String(60), nullable=False)
    on_image_subhead: Mapped[str] = mapped_column(String(90), nullable=False, default="")
    on_image_kicker: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    image_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Serialized ReelScript ({scenes: [{visual_prompt, vo_line, duration_s,
    # on_screen_text}, ...]}) -- only set when the brief's format is "reel".
    reel_script: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Serialized list[CarouselSlide] -- only set when format is "carousel".
    carousel_slides: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    disclaimer_line: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compliance_notes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Set by the compliance gate -- distinct from status below, which is
    # the separate human draft/review/approve/schedule/publish workflow
    # (Phase 19/20).
    compliance_rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    compliance_rejection_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ContentStatus] = mapped_column(
        enum_column_type(ContentStatus), nullable=False, default=ContentStatus.draft
    )
    # Generic "last actioned by/at" for every status transition (submit,
    # approve, reject, schedule, publish) -- full per-transition history
    # already lives in the audit log, so per-transition columns would just
    # duplicate it.
    reviewed_by: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
