import structlog

from app.modules.email.base import EmailService

# Deliberately its own logger, separate from the app's general request
# logger (app.core.logging_setup), which must never contain tokens or
# passwords. This one exists specifically so a token/credential normally
# only ever delivered by email is visible somewhere during local development.
_logger = structlog.get_logger("email")


class ConsoleEmailService(EmailService):
    async def send_verification_otp(self, *, to_email: str, otp: str) -> None:
        _logger.info(
            "email.verification",
            to=to_email,
            otp=otp,
            hint="POST /api/v1/auth/verify-email with this code",
        )

    async def send_password_reset_email(self, *, to_email: str, otp: str) -> None:
        _logger.info(
            "email.password_reset",
            to=to_email,
            otp=otp,
            hint="POST /api/v1/auth/verify-reset-otp with this code",
        )

    async def send_new_member_credentials(
        self, *, to_email: str, temporary_password: str, company_name: str
    ) -> None:
        _logger.info(
            "email.new_member_credentials",
            to=to_email,
            temporary_password=temporary_password,
            company_name=company_name,
            hint="POST /api/v1/auth/login with this email and temporary password",
        )

    async def send_company_approved_email(self, *, to_email: str, company_name: str) -> None:
        _logger.info(
            "email.company_approved",
            to=to_email,
            company_name=company_name,
        )

    async def send_company_rejected_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        _logger.info(
            "email.company_rejected",
            to=to_email,
            company_name=company_name,
            reason=reason,
        )

    async def send_company_suspended_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        _logger.info(
            "email.company_suspended",
            to=to_email,
            company_name=company_name,
            reason=reason,
        )

    async def send_company_deleted_email(self, *, to_email: str, company_name: str) -> None:
        _logger.info(
            "email.company_deleted",
            to=to_email,
            company_name=company_name,
        )
