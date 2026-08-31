from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import (
    ContentApprovalPolicy,
    ContentStatus,
    ProductBrandingMode,
    ProductRole,
    ProductStatus,
)


class ProductPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    name: str
    slug: str
    description: str | None
    status: ProductStatus
    branding_mode: ProductBrandingMode
    approval_policy: ContentApprovalPolicy
    created_by: str
    created_at: UTCDatetime
    updated_at: UTCDatetime


class CreateProductRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # Phase 7's "Use company branding" / "Create a separate product brand"
    # radio choice -- defaults to separate_brand, matching the model.
    branding_mode: ProductBrandingMode = ProductBrandingMode.separate_brand


class UpdateProductRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProductStatus | None = None
    branding_mode: ProductBrandingMode | None = None
    # Phase 20's Company-Admin-configurable approval policy.
    approval_policy: ContentApprovalPolicy | None = None


class ProductMemberPublic(BaseModel):
    id: str
    product_id: str
    user_id: str
    role: ProductRole
    sub_product_ids: list[str]
    added_by: str
    created_at: UTCDatetime
    user_email: str
    user_full_name: str


class AddProductMemberRequest(BaseModel):
    user_id: str
    role: ProductRole
    sub_product_ids: list[str] = Field(default_factory=list)


class ConceptSummary(BaseModel):
    """A lightweight row for the dashboard's content lists -- not the full
    CreativeConceptPublic (no caption/hashtags/assets), just enough to link
    out to the concept from the dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    hook: str
    status: ContentStatus
    scheduled_at: UTCDatetime | None
    published_at: UTCDatetime | None
    updated_at: UTCDatetime


class DashboardSummary(BaseModel):
    """Phase 13's dashboard cards."""

    product_id: str
    drafts: int
    pending_approvals: int
    scheduled: int
    published: int
    failed_jobs: int
    social_accounts_total: int
    social_accounts_active: int
    # Phase 13's dashboard sections. top_performing is "most recently
    # published" (this backend has no engagement/analytics tracking at all
    # -- publishing is state-only, see CreativeService.publish_concept) --
    # labeled honestly rather than faking engagement numbers.
    recent_content: list[ConceptSummary] = Field(default_factory=list)
    pending_approvals_list: list[ConceptSummary] = Field(default_factory=list)
    upcoming_scheduled: list[ConceptSummary] = Field(default_factory=list)
    top_performing: list[ConceptSummary] = Field(default_factory=list)
    ai_recommendations: list[str] = Field(default_factory=list)


class InviteProductMemberRequest(BaseModel):
    """Phase 11's team invite: by email, may or may not have an account yet
    -- unlike AddProductMemberRequest, which requires an existing user_id
    and an existing company membership."""

    email: EmailStr
    # Only required when `email` doesn't match an existing user.
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: ProductRole
    # Empty means "all sub-products", same convention as ProductMember's
    # own column.
    sub_product_ids: list[str] = Field(default_factory=list)
