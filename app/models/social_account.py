from sqlalchemy import CHAR, JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import (
    SocialAccountScope,
    SocialAccountStatus,
    SocialConnectionMethod,
    SocialPlatform,
)


class SocialAccount(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "platform", "handle", name="uq_social_accounts_product_platform_handle"
        ),
    )

    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=False, index=True
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        enum_column_type(SocialPlatform), nullable=False
    )
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SocialAccountStatus] = mapped_column(
        enum_column_type(SocialAccountStatus), nullable=False, default=SocialAccountStatus.active
    )
    # Phase 10's "Where should this account be available?" choice -- product
    # (default), sub_products, or the entire company.
    scope: Mapped[SocialAccountScope] = mapped_column(
        enum_column_type(SocialAccountScope), nullable=False, default=SocialAccountScope.product
    )
    # Only meaningful when scope=sub_products -- empty means "all
    # sub-products", same convention as ProductMember.sub_product_ids.
    sub_product_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Always "manual" today (see the "manual entry" scope decision for this
    # phase) -- the column exists so a row is self-describing if/when real
    # OAuth is added later, without a schema change.
    connection_method: Mapped[SocialConnectionMethod] = mapped_column(
        enum_column_type(SocialConnectionMethod),
        nullable=False,
        default=SocialConnectionMethod.manual,
    )
    added_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
