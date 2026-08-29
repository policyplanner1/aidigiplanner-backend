from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_super_admin
from app.modules.audit.schemas import AuditLogListResponse, AuditLogPublic
from app.modules.audit.service import AuditService

router = APIRouter(
    prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(require_super_admin())]
)


def get_audit_service(session: DbSession) -> AuditService:
    return AuditService(session)


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    service: AuditServiceDep,
    action: str | None = None,
    actor_user_id: str | None = None,
    company_id: str | None = None,
    product_id: str | None = None,
    resource_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogListResponse:
    return await service.list_logs(
        action=action,
        actor_user_id=actor_user_id,
        company_id=company_id,
        product_id=product_id,
        resource_type=resource_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/{log_id}", response_model=AuditLogPublic)
async def get_audit_log(log_id: str, service: AuditServiceDep) -> AuditLogPublic:
    return await service.get_log(log_id)
