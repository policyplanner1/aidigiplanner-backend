from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, CreatedAtMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import RefreshTokenRevokedReason


class RefreshToken(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[str] = mapped_column(CHAR(36), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("refresh_tokens.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    revoked_reason: Mapped[RefreshTokenRevokedReason | None] = mapped_column(
        enum_column_type(RefreshTokenRevokedReason), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
