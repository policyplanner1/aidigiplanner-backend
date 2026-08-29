from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, CreatedAtMixin, UUIDPKMixin


class EmailVerificationOtp(UUIDPKMixin, CreatedAtMixin, Base):
    """The emailed 6-digit code shown on the "Verify your email" screen.
    Same shape/lockout semantics as PasswordResetOtp -- see
    AuthService.verify_email."""

    __tablename__ = "email_verification_otps"

    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False, index=True
    )
    # Unlike the 48-byte opaque tokens elsewhere, a 6-digit code isn't
    # unique-by-construction -- no unique constraint here.
    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
