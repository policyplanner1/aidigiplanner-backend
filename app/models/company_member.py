from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import CompanyMemberStatus, CompanyRole


class CompanyMember(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "company_members"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_company_members_company_user"),
    )

    company_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("companies.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[CompanyRole] = mapped_column(enum_column_type(CompanyRole), nullable=False)
    status: Mapped[CompanyMemberStatus] = mapped_column(
        enum_column_type(CompanyMemberStatus),
        nullable=False,
        default=CompanyMemberStatus.active,
    )
    invited_by: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    joined_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
