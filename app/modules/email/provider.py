from functools import lru_cache

from app.core.config import get_settings
from app.modules.email.base import EmailService
from app.modules.email.console import ConsoleEmailService
from app.modules.email.smtp import SmtpEmailService


@lru_cache
def get_email_service() -> EmailService:
    settings = get_settings()
    if settings.smtp_host:
        return SmtpEmailService()
    return ConsoleEmailService()
