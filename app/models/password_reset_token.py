from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, CreatedAtMixin, UUIDPKMixin


class PasswordResetToken(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
