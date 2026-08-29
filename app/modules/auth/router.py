from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import rate_limit_by_email, rate_limit_by_ip
from app.modules.audit.service import AuditService
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    CompanyPublic,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenPair,
    UserPublic,
    VerifyEmailRequest,
    VerifyResetOtpRequest,
    VerifyResetOtpResponse,
)
from app.modules.auth.service import AuthService
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_service(
    session: DbSession, email: Annotated[EmailService, Depends(get_email_service)]
) -> AuthService:
    """FastAPI dependency that wires up AuthService with its DB session, audit logger, and
    email backend."""
    return AuthService(session=session, audit=AuditService(session), email=email)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _client_ip(request: Request) -> str | None:
    """Extracts the caller's IP for rate limiting and audit logging; None if unavailable
    (e.g. in tests)."""
    return request.client.host if request.client else None


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    dependencies=[Depends(rate_limit_by_ip("register"))],
)
async def register(
    payload: RegisterRequest, request: Request, auth_service: AuthServiceDep
) -> RegisterResponse:
    """Creates a new user + company. Rate limited by both IP (route dependency) and
    email (explicit check)."""
    await rate_limit_by_email(payload.email, "register")
    user, company = await auth_service.register(
        payload, ip_address=_client_ip(request), user_agent=request.headers.get("user-agent")
    )
    return RegisterResponse(
        user=UserPublic.model_validate(user), company=CompanyPublic.model_validate(company)
    )


@router.post(
    "/login", response_model=TokenPair, dependencies=[Depends(rate_limit_by_ip("login"))]
)
async def login(
    payload: LoginRequest, request: Request, auth_service: AuthServiceDep
) -> TokenPair:
    """Authenticates by email/password and issues an access + refresh token pair."""
    await rate_limit_by_email(payload.email, "login")
    return await auth_service.login(
        payload.email,
        payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/refresh", response_model=TokenPair, dependencies=[Depends(rate_limit_by_ip("refresh"))]
)
async def refresh(
    payload: RefreshRequest, request: Request, auth_service: AuthServiceDep
) -> TokenPair:
    """Exchanges a valid refresh token for a new access + refresh token pair (no login required)."""
    return await auth_service.refresh(
        payload.refresh_token,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    payload: LogoutRequest, current_user: CurrentUser, auth_service: AuthServiceDep
) -> MessageResponse:
    """Revokes a single refresh token (the current session/device) for the authenticated user."""
    await auth_service.logout(current_user, payload.refresh_token)
    return MessageResponse(message="Logged out.")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(current_user: CurrentUser, auth_service: AuthServiceDep) -> MessageResponse:
    """Revokes every refresh token for the user, logging them out of all devices/sessions."""
    await auth_service.logout_all(current_user)
    return MessageResponse(message="Logged out of all devices.")


@router.get("/me", response_model=MeResponse)
async def me(current_user: CurrentUser, auth_service: AuthServiceDep) -> MeResponse:
    """Returns the authenticated user's profile, used by the frontend to hydrate session state."""
    return await auth_service.me(current_user)


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit_by_ip("verify-email"))],
)
async def verify_email(
    payload: VerifyEmailRequest, auth_service: AuthServiceDep
) -> MessageResponse:
    """Confirms a user's email using the 6-digit code sent at registration."""
    await auth_service.verify_email(payload.email, payload.otp)
    return MessageResponse(message="Email verified. You can now log in.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit_by_ip("resend-verification"))],
)
async def resend_verification(
    payload: ResendVerificationRequest, auth_service: AuthServiceDep
) -> MessageResponse:
    """Re-sends the verification email. Response message is intentionally generic to avoid leaking
    whether the email is registered."""
    await rate_limit_by_email(payload.email, "resend-verification")
    await auth_service.resend_verification(payload.email)
    return MessageResponse(
        message="If an account with that email exists, a verification link has been sent."
    )


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit_by_ip("forgot-password"))],
)
async def forgot_password(
    payload: ForgotPasswordRequest, auth_service: AuthServiceDep
) -> MessageResponse:
    """Starts the password reset flow by emailing an OTP. Generic response avoids
    leaking account existence."""
    await rate_limit_by_email(payload.email, "forgot-password")
    await auth_service.forgot_password(payload.email)
    return MessageResponse(
        message="If an account with that email exists, a password reset code has been sent."
    )


@router.post(
    "/verify-reset-otp",
    response_model=VerifyResetOtpResponse,
    dependencies=[Depends(rate_limit_by_ip("verify-reset-otp"))],
)
async def verify_reset_otp(
    payload: VerifyResetOtpRequest, auth_service: AuthServiceDep
) -> VerifyResetOtpResponse:
    """Validates the OTP from forgot-password and exchanges it for a short-lived reset_token
    used by reset-password."""
    await rate_limit_by_email(payload.email, "verify-reset-otp")
    reset_token = await auth_service.verify_reset_otp(payload.email, payload.otp)
    return VerifyResetOtpResponse(reset_token=reset_token)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit_by_ip("reset-password"))],
)
async def reset_password(
    payload: ResetPasswordRequest, auth_service: AuthServiceDep
) -> MessageResponse:
    """Sets a new password using the reset_token obtained from verify-reset-otp. No auth
    required."""
    await auth_service.reset_password(payload.reset_token, payload.new_password)
    return MessageResponse(message="Password reset. You can now log in with your new password.")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest, current_user: CurrentUser, auth_service: AuthServiceDep
) -> MessageResponse:
    """Changes password for an already-authenticated user (requires current_password)."""
    await auth_service.change_password(
        current_user, payload.current_password, payload.new_password
    )
    return MessageResponse(message="Password changed. Please log in again.")
