from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import CompanyBrandStructure, CompanyOnboardingStep


def _validate_password_strength(value: str) -> str:
    if len(value) < 10:
        raise ValueError("Password must be at least 10 characters long.")
    return value


PasswordStr = Annotated[str, AfterValidator(_validate_password_strength)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: PasswordStr
    full_name: str = Field(min_length=1, max_length=255)
    company_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


class VerifyResetOtpResponse(BaseModel):
    # Single-use, short-lived — pass this to /reset-password. The OTP
    # itself is consumed by this call and can't be reused.
    reset_token: str


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: PasswordStr


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: PasswordStr


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    # Populated on login only, and mutually exclusive: a company_admin gets
    # company_id, a plain member gets member_id (their CompanyMember row's
    # id). Both stay None for a Super Admin (no company membership) and for
    # refresh responses (the client already has these from the original login).
    company_id: str | None = None
    member_id: str | None = None


class MessageResponse(BaseModel):
    message: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    is_super_admin: bool
    status: str
    email_verified_at: UTCDatetime | None
    created_at: UTCDatetime


class CompanyPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: str
    brand_structure: CompanyBrandStructure | None
    onboarding_step: CompanyOnboardingStep
    group_website_url: str | None
    group_logo_mime_type: str | None
    created_at: UTCDatetime


class RegisterResponse(BaseModel):
    user: UserPublic
    company: CompanyPublic


class CompanyMembershipPublic(BaseModel):
    company_id: str
    company_name: str
    company_slug: str
    role: str
    status: str


class ProductAccessPublic(BaseModel):
    product_id: str
    product_name: str
    company_id: str
    role: str


class MeResponse(BaseModel):
    user: UserPublic
    companies: list[CompanyMembershipPublic]
    products: list[ProductAccessPublic]
