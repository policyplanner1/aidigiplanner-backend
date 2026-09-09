from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_by_ip
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service
from app.modules.public.schemas import DemoRequest, DemoRequestResponse

router = APIRouter(prefix="/api/v1/public", tags=["public"])

EmailDep = Annotated[EmailService, Depends(get_email_service)]


@router.post(
    "/demo-requests",
    response_model=DemoRequestResponse,
    status_code=201,
    dependencies=[Depends(rate_limit_by_ip("demo"))],
)
async def create_demo_request(
    payload: DemoRequest, request: Request, email: EmailDep
) -> DemoRequestResponse:
    settings = get_settings()
    inbox = settings.smtp_user
    if not inbox:
        return DemoRequestResponse(message="Demo request received.")

    note = payload.message.strip() or "(no message)"
    try:
        await email.send_demo_request(
            to_email=inbox,
            name=payload.name.strip(),
            work_email=str(payload.email).strip().lower(),
            company=payload.company.strip(),
            message=note,
            ip_address=request.client.host if request.client else None,
        )
    except Exception:
        raise AppError(
            "Could not send the demo request. Please try again.",
            code="email_send_failed",
        )
    return DemoRequestResponse(message="Demo request received.")
