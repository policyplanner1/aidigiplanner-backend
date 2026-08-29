from typing import Any

from sqlalchemy import CHAR, JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDPKMixin


class AuditLog(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_company_id_created_at", "company_id", "created_at"),
        Index("ix_audit_logs_actor_user_id_created_at", "actor_user_id", "created_at"),
    )

    actor_user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    company_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("companies.id"), nullable=True
    )
    product_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Mapped to the `metadata` column but the Python attribute can't be named
    # `metadata` — that name is reserved by SQLAlchemy's declarative Base.
    audit_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
