from abc import ABC, abstractmethod


class EmailService(ABC):
    """Provider-agnostic boundary for outbound transactional email.

    Two implementations exist — ConsoleEmailService (logs instead of
    sending) and SmtpEmailService (real SMTP) — selected by
    app.modules.email.provider.get_email_service() based on config.
    Anything that needs to send email should depend on this interface, not
    a concrete implementation.
    """

    @abstractmethod
    async def send_verification_otp(self, *, to_email: str, otp: str) -> None: ...

    @abstractmethod
    async def send_password_reset_email(self, *, to_email: str, otp: str) -> None: ...

    @abstractmethod
    async def send_new_member_credentials(
        self, *, to_email: str, temporary_password: str, company_name: str
    ) -> None: ...

    @abstractmethod
    async def send_company_approved_email(self, *, to_email: str, company_name: str) -> None: ...

    @abstractmethod
    async def send_company_rejected_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None: ...

    @abstractmethod
    async def send_company_suspended_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None: ...

    @abstractmethod
    async def send_company_deleted_email(self, *, to_email: str, company_name: str) -> None: ...
