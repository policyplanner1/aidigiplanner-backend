import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from app.core.config import get_settings
from app.modules.email.base import EmailService

_logger = structlog.get_logger("email")


class SmtpEmailService(EmailService):
    """Real outbound email over SMTP (e.g. Gmail + an App Password).

    smtplib is blocking, so every send runs in a worker thread via
    asyncio.to_thread — keeps the EmailService interface async without
    pulling in an extra async-SMTP dependency.
    """

    def _send_sync(self, *, to_email: str, subject: str, body: str) -> None:
        settings = get_settings()
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
        message["To"] = to_email
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.send_message(message)

    async def _send(self, *, to_email: str, subject: str, body: str) -> None:
        try:
            await asyncio.to_thread(self._send_sync, to_email=to_email, subject=subject, body=body)
            _logger.info("email.sent", to=to_email, subject=subject)
        except Exception:
            # Best-effort: the DB change that triggered this email (register,
            # add-member, approve/reject) already committed. Log loudly
            # instead of failing the request — affected flows all have a
            # retry path (resend-verification, forgot-password, or the admin
            # just re-adding the member).
            _logger.exception("email.send_failed", to=to_email, subject=subject)

    async def send_verification_otp(self, *, to_email: str, otp: str) -> None:
        settings = get_settings()
        await self._send(
            to_email=to_email,
            subject="Verify your email — AI Social Planner",
            body=(
                "Welcome to AI Social Planner!\n\n"
                f"Your verification code is:\n\n{otp}\n\n"
                f"Enter this code to continue. It expires in "
                f"{settings.email_verification_otp_ttl_minutes} minutes and can only be "
                "used once. If you didn't create this account, you can safely ignore "
                "this email."
            ),
        )

    async def send_password_reset_email(self, *, to_email: str, otp: str) -> None:
        settings = get_settings()
        await self._send(
            to_email=to_email,
            subject="Your password reset code — AI Social Planner",
            body=(
                "We received a request to reset your AI Social Planner password.\n\n"
                f"Your verification code is:\n\n{otp}\n\n"
                f"Enter this code to continue. It expires in {settings.otp_ttl_minutes} "
                "minutes and can only be used once. If you didn't request this, "
                "you can safely ignore this email — your password won't be changed."
            ),
        )

    async def send_new_member_credentials(
        self, *, to_email: str, temporary_password: str, company_name: str
    ) -> None:
        await self._send(
            to_email=to_email,
            subject=f"You've been added to {company_name} on AI Social Planner",
            body=(
                f"You've been added as a member of {company_name} on AI Social Planner.\n\n"
                f"Email: {to_email}\n"
                f"Temporary password: {temporary_password}\n\n"
                "Please log in and change your password as soon as possible."
            ),
        )

    async def send_company_approved_email(self, *, to_email: str, company_name: str) -> None:
        await self._send(
            to_email=to_email,
            subject=f"{company_name} has been approved — AI Social Planner",
            body=(
                f"Good news — {company_name} has been reviewed and approved.\n\n"
                "You can now log in and start using AI Social Planner."
            ),
        )

    async def send_company_rejected_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        await self._send(
            to_email=to_email,
            subject=f"{company_name} registration was not approved — AI Social Planner",
            body=(
                f"Your registration for {company_name} on AI Social Planner was not "
                "approved.\n\n"
                f"Reason: {reason}\n\n"
                "If you believe this is a mistake, please contact support."
            ),
        )

    async def send_company_suspended_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        await self._send(
            to_email=to_email,
            subject=f"{company_name} has been suspended — AI Social Planner",
            body=(
                f"{company_name} has been suspended on AI Social Planner.\n\n"
                f"Reason: {reason}\n\n"
                "Members of this company will not be able to log in until it is "
                "reinstated. If you believe this is a mistake, please contact support."
            ),
        )

    async def send_company_deleted_email(self, *, to_email: str, company_name: str) -> None:
        await self._send(
            to_email=to_email,
            subject=f"{company_name} has been removed — AI Social Planner",
            body=(
                f"{company_name} has been removed from AI Social Planner by a Super Admin.\n\n"
                "Members of this company will no longer be able to log in. If you "
                "believe this is a mistake, please contact support."
            ),
        )
