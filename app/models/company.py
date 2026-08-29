from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import CompanyBrandStructure, CompanyOnboardingStep, CompanyStatus


class Company(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # New companies wait here for Super Admin review — see
    # AuthService.register and app.modules.admin.
    status: Mapped[CompanyStatus] = mapped_column(
        enum_column_type(CompanyStatus), nullable=False, default=CompanyStatus.pending_approval
    )
    approved_by: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Onboarding (Phases 3-13) -- independent of the approval gate above.
    # None until Phase 3's brand-structure screen is answered.
    brand_structure: Mapped[CompanyBrandStructure | None] = mapped_column(
        enum_column_type(CompanyBrandStructure), nullable=True
    )
    onboarding_step: Mapped[CompanyOnboardingStep] = mapped_column(
        enum_column_type(CompanyOnboardingStep),
        nullable=False,
        default=CompanyOnboardingStep.registered,
    )
    # Phase 4B's multi-brand "Group profile" -- optional, company-wide,
    # deliberately separate from the single-brand path's full analyzed
    # profile (app.models.brand_profile.BrandProfile at scope=company).
    group_website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    group_logo_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    group_logo_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
