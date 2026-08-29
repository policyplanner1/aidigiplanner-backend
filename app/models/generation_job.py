from datetime import datetime

from sqlalchemy import CHAR, Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import GenerationJobStatus


class GenerationJob(UUIDPKMixin, TimestampMixin, Base):
    """Async job tracking row for one creative-generation run. Append-only
    audit trail -- no soft delete. product_id/company_id are denormalized
    off of brief_id so the status-poll endpoint and the per-company
    daily-cost guardrail query never need a join."""

    __tablename__ = "generation_jobs"

    brief_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("creative_briefs.id"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("companies.id"), nullable=False, index=True
    )
    status: Mapped[GenerationJobStatus] = mapped_column(
        enum_column_type(GenerationJobStatus), nullable=False, default=GenerationJobStatus.queued
    )
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Forces the mock provider even when a real GEMINI_API_KEY is configured
    # -- lets a caller exercise the pipeline at zero cost on demand. Stored
    # here (not on CreativeBrief) since it's an execution-mode choice, not
    # part of the brief's own identity/idempotency key.
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimated_cost_inr: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    actual_cost_inr: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    # arq's own job id, for cross-referencing arq's inspection/retry tooling.
    arq_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
