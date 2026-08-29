from sqlalchemy import CHAR, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import ContentApprovalPolicy, ProductBrandingMode, ProductStatus


class Product(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("company_id", "slug", name="uq_products_company_slug"),)

    company_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("companies.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        enum_column_type(ProductStatus), nullable=False, default=ProductStatus.active
    )
    # Mirrors SubProduct.branding_mode -- use_company_branding means this
    # product has no BrandProfile row of its own and instead resolves to
    # its company's (see resolve_effective_brand_profile).
    branding_mode: Mapped[ProductBrandingMode] = mapped_column(
        enum_column_type(ProductBrandingMode),
        nullable=False,
        default=ProductBrandingMode.separate_brand,
    )
    approval_policy: Mapped[ContentApprovalPolicy] = mapped_column(
        enum_column_type(ContentApprovalPolicy),
        nullable=False,
        default=ContentApprovalPolicy.no_approval,
    )
    created_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
