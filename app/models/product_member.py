from sqlalchemy import CHAR, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import ProductRole


class ProductMember(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "product_members"
    __table_args__ = (
        UniqueConstraint("product_id", "user_id", name="uq_product_members_product_user"),
    )

    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[ProductRole] = mapped_column(enum_column_type(ProductRole), nullable=False)
    # Empty list means "all sub-products" (see Phase 11's "All sub-products"
    # team-invite dropdown) -- a non-empty list restricts this member to
    # only those sub-product ids under `product_id`.
    sub_product_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    added_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
