from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import (
    CompanyBrandStructure,
    CompanyMemberStatus,
    CompanyOnboardingStep,
    CompanyRole,
)


class CompanyMemberPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    user_id: str
    role: CompanyRole
    status: CompanyMemberStatus
    invited_by: str | None
    joined_at: UTCDatetime | None
    created_at: UTCDatetime
    user_email: str
    user_full_name: str
    # Names of the products (within this company) this user is assigned to.
    # None if they're not on any product yet.
    products: list[str] | None = None


class AddCompanyMemberRequest(BaseModel):
    email: EmailStr
    # Only required when `email` doesn't match an existing user — used to
    # create their account before emailing them generated credentials.
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: CompanyRole


class UpdateCompanyMemberRequest(BaseModel):
    role: CompanyRole | None = None
    status: CompanyMemberStatus | None = None


class SelectBrandStructureRequest(BaseModel):
    """Phase 3's "How does your company manage its brands?" screen."""

    brand_structure: CompanyBrandStructure


class UpdateSingleBrandDetailsRequest(BaseModel):
    """Phase 4A's single-brand company details -- `industry` is the
    admin's own dropdown choice (not AI-inferred), stored on the
    company-scope BrandProfile's `category` field alongside whatever
    POST .../brand-profile/analyze later fills in."""

    industry: str = Field(min_length=1, max_length=255)


class UpdateGroupProfileRequest(BaseModel):
    """Phase 4B's multi-brand "Group profile" -- both fields optional."""

    group_website_url: str | None = Field(default=None, max_length=500)


class OnboardingStatus(BaseModel):
    company_id: str
    onboarding_step: CompanyOnboardingStep
    brand_structure: CompanyBrandStructure | None
    group_website_url: str | None
    products_count: int
    sub_products_count: int
    social_accounts_count: int
    team_members_count: int
