from sqlalchemy import CHAR, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import ProductStatus, SubProductBrandingMode


class SubProduct(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sub_products"
    __table_args__ = (
        UniqueConstraint("product_id", "slug", name="uq_sub_products_product_slug"),
    )

    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ProductStatus] = mapped_column(
        enum_column_type(ProductStatus), nullable=False, default=ProductStatus.active
    )
    # Sub-products inherit the parent product's branding/tone/audience/CTA/
    # compliance by default (see Phase 9) -- separate_brand opts one out into
    # its own BrandProfile row (see resolve_effective_brand_profile).
    branding_mode: Mapped[SubProductBrandingMode] = mapped_column(
        enum_column_type(SubProductBrandingMode),
        nullable=False,
        default=SubProductBrandingMode.use_product_branding,
    )
    created_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
