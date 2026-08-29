from pydantic import BaseModel, ConfigDict, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import (
    CompanyMemberStatus,
    CompanyRole,
    CompanyStatus,
    ProductRole,
    SocialPlatform,
    UserStatus,
)
from app.modules.companies.schemas import CompanyMemberPublic
from app.modules.social_accounts.schemas import SocialAccountPublic


class CompanySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: CompanyStatus
    created_at: UTCDatetime
    approved_at: UTCDatetime | None
    rejected_at: UTCDatetime | None


class CompanyDetail(CompanySummary):
    rejection_reason: str | None
    members: list[CompanyMemberPublic]
    social_accounts: list[SocialAccountPublic]


class RejectCompanyRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class SuspendCompanyRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class UserCompanyMembership(BaseModel):
    company_id: str
    company_name: str
    company_slug: str
    role: CompanyRole
    status: CompanyMemberStatus
    joined_at: UTCDatetime | None


class UserProductMembership(BaseModel):
    product_id: str
    product_name: str
    product_slug: str
    company_id: str
    role: ProductRole


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    is_super_admin: bool
    status: UserStatus
    email_verified_at: UTCDatetime | None
    last_login_at: UTCDatetime | None
    created_at: UTCDatetime
    companies: list[UserCompanyMembership]
    products: list[UserProductMembership]


class UserDetail(UserSummary):
    social_accounts: list[SocialAccountPublic]


class UserListResponse(BaseModel):
    items: list[UserSummary]
    total: int
    limit: int
    offset: int


class CompanyKPIs(BaseModel):
    total: int
    pending_approval: int
    active: int
    rejected: int
    suspended: int


class UserKPIs(BaseModel):
    total: int
    active: int
    pending: int
    suspended: int
    super_admins: int


class SocialAccountKPIs(BaseModel):
    total: int
    active: int
    disabled: int
    by_platform: dict[SocialPlatform, int]


class KPISummary(BaseModel):
    companies: CompanyKPIs
    users: UserKPIs
    social_accounts: SocialAccountKPIs
