from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import UserStatus


class User(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[UserStatus] = mapped_column(
        enum_column_type(UserStatus), nullable=False, default=UserStatus.pending
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
