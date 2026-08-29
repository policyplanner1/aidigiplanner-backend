from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status

from app.core.deps import CurrentUser, DbSession, require_company_role
from app.models.enums import CompanyRole
from app.modules.audit.service import AuditService
from app.modules.auth.schemas import CompanyPublic
from app.modules.companies.schemas import (
    AddCompanyMemberRequest,
    CompanyMemberPublic,
    OnboardingStatus,
    SelectBrandStructureRequest,
    UpdateCompanyMemberRequest,
    UpdateGroupProfileRequest,
    UpdateSingleBrandDetailsRequest,
)
from app.modules.companies.service import CompanyMemberService, CompanyService
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service

router = APIRouter(tags=["companies"])


def get_company_member_service(
    session: DbSession, email: Annotated[EmailService, Depends(get_email_service)]
) -> CompanyMemberService:
    return CompanyMemberService(session=session, audit=AuditService(session), email=email)


CompanyMemberServiceDep = Annotated[CompanyMemberService, Depends(get_company_member_service)]


def get_company_service(
    session: DbSession, storage: Annotated[StorageService, Depends(get_storage_service)]
) -> CompanyService:
    return CompanyService(session=session, audit=AuditService(session), storage=storage)


CompanyServiceDep = Annotated[CompanyService, Depends(get_company_service)]


@router.get(
    "/api/v1/companies/{company_id}/members",
    response_model=list[CompanyMemberPublic],
    dependencies=[Depends(require_company_role())],
)
async def list_company_members(
    company_id: str, service: CompanyMemberServiceDep
) -> list[CompanyMemberPublic]:
    return await service.list_members(company_id)


@router.post(
    "/api/v1/companies/{company_id}/members",
    response_model=CompanyMemberPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def add_company_member(
    company_id: str,
    payload: AddCompanyMemberRequest,
    current_user: CurrentUser,
    service: CompanyMemberServiceDep,
) -> CompanyMemberPublic:
    return await service.add_member(company_id, current_user, payload)


@router.patch(
    "/api/v1/companies/{company_id}/members/{member_id}",
    response_model=CompanyMemberPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def update_company_member(
    company_id: str,
    member_id: str,
    payload: UpdateCompanyMemberRequest,
    current_user: CurrentUser,
    service: CompanyMemberServiceDep,
) -> CompanyMemberPublic:
    return await service.update_member(company_id, member_id, current_user, payload)


@router.delete(
    "/api/v1/companies/{company_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def remove_company_member(
    company_id: str, member_id: str, current_user: CurrentUser, service: CompanyMemberServiceDep
) -> Response:
    await service.remove_member(company_id, member_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/api/v1/companies/{company_id}/brand-structure",
    response_model=CompanyPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def select_brand_structure(
    company_id: str,
    payload: SelectBrandStructureRequest,
    current_user: CurrentUser,
    service: CompanyServiceDep,
) -> CompanyPublic:
    company = await service.select_brand_structure(company_id, current_user, payload)
    return CompanyPublic.model_validate(company)


@router.patch(
    "/api/v1/companies/{company_id}/single-brand-details",
    response_model=CompanyPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def update_single_brand_details(
    company_id: str,
    payload: UpdateSingleBrandDetailsRequest,
    current_user: CurrentUser,
    service: CompanyServiceDep,
) -> CompanyPublic:
    company = await service.update_single_brand_details(company_id, current_user, payload)
    return CompanyPublic.model_validate(company)


@router.patch(
    "/api/v1/companies/{company_id}/group-profile",
    response_model=CompanyPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def update_group_profile(
    company_id: str,
    payload: UpdateGroupProfileRequest,
    current_user: CurrentUser,
    service: CompanyServiceDep,
) -> CompanyPublic:
    company = await service.update_group_profile(company_id, current_user, payload)
    return CompanyPublic.model_validate(company)


@router.put(
    "/api/v1/companies/{company_id}/group-profile/logo",
    response_model=CompanyPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def upload_group_logo(
    company_id: str,
    current_user: CurrentUser,
    service: CompanyServiceDep,
    file: Annotated[UploadFile, File()],
) -> CompanyPublic:
    data = await file.read()
    company = await service.upload_group_logo(
        company_id, current_user, data=data, content_type=file.content_type or ""
    )
    return CompanyPublic.model_validate(company)


@router.post(
    "/api/v1/companies/{company_id}/onboarding/complete",
    response_model=CompanyPublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def complete_onboarding(
    company_id: str, current_user: CurrentUser, service: CompanyServiceDep
) -> CompanyPublic:
    company = await service.complete_onboarding(company_id, current_user)
    return CompanyPublic.model_validate(company)


@router.get(
    "/api/v1/companies/{company_id}/onboarding",
    response_model=OnboardingStatus,
    dependencies=[Depends(require_company_role())],
)
async def get_onboarding_status(
    company_id: str, service: CompanyServiceDep
) -> OnboardingStatus:
    return await service.get_onboarding_status(company_id)
