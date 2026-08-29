from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.deps import CurrentUser, DbSession, require_super_admin
from app.models.enums import CompanyStatus, UserStatus
from app.modules.admin.schemas import (
    CompanyDetail,
    CompanySummary,
    KPISummary,
    RejectCompanyRequest,
    SuspendCompanyRequest,
    UserDetail,
    UserListResponse,
)
from app.modules.admin.service import AdminService
from app.modules.audit.service import AuditService
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service

router = APIRouter(
    prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_super_admin())]
)


def get_admin_service(
    session: DbSession, email: Annotated[EmailService, Depends(get_email_service)]
) -> AdminService:
    return AdminService(session=session, audit=AuditService(session), email=email)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


@router.get("/companies", response_model=list[CompanySummary])
async def list_companies(
    service: AdminServiceDep, status: CompanyStatus | None = None
) -> list[CompanySummary]:
    companies = await service.list_companies(status)
    return [CompanySummary.model_validate(c) for c in companies]


@router.get("/companies/{company_id}", response_model=CompanyDetail)
async def get_company(company_id: str, service: AdminServiceDep) -> CompanyDetail:
    return await service.get_company_detail(company_id)


@router.post("/companies/{company_id}/approve", response_model=CompanySummary)
async def approve_company(
    company_id: str, current_user: CurrentUser, service: AdminServiceDep
) -> CompanySummary:
    company = await service.approve_company(company_id, current_user)
    return CompanySummary.model_validate(company)


@router.post("/companies/{company_id}/reject", response_model=CompanySummary)
async def reject_company(
    company_id: str,
    payload: RejectCompanyRequest,
    current_user: CurrentUser,
    service: AdminServiceDep,
) -> CompanySummary:
    company = await service.reject_company(company_id, current_user, payload)
    return CompanySummary.model_validate(company)


@router.post("/companies/{company_id}/suspend", response_model=CompanySummary)
async def suspend_company(
    company_id: str,
    payload: SuspendCompanyRequest,
    current_user: CurrentUser,
    service: AdminServiceDep,
) -> CompanySummary:
    company = await service.suspend_company(company_id, current_user, payload)
    return CompanySummary.model_validate(company)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: str, current_user: CurrentUser, service: AdminServiceDep
) -> Response:
    await service.delete_company(company_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=UserListResponse)
async def list_users(
    service: AdminServiceDep,
    company_id: str | None = None,
    status: UserStatus | None = None,
    is_super_admin: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    return await service.list_users(
        company_id=company_id,
        status_filter=status,
        is_super_admin=is_super_admin,
        limit=limit,
        offset=offset,
    )


@router.get("/users/{user_id}", response_model=UserDetail)
async def get_user(user_id: str, service: AdminServiceDep) -> UserDetail:
    return await service.get_user_detail(user_id)


@router.get("/kpis", response_model=KPISummary)
async def get_kpis(service: AdminServiceDep) -> KPISummary:
    return await service.get_kpis()
