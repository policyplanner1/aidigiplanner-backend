from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import (
    CurrentUser,
    DbSession,
    require_company_role,
    require_product_access,
    require_sub_product_access,
)
from app.models.enums import BrandAnalysisScope, CompanyRole, ProductRole
from app.modules.audit.service import AuditService
from app.modules.brand_analysis.schemas import AnalyzeBrandRequest
from app.modules.brand_analysis.service import BrandAnalysisService
from app.modules.brand_profiles.schemas import BrandProfilePublic
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service

router = APIRouter(tags=["brand-analysis"])

StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]


def get_brand_analysis_service(
    session: DbSession, storage: StorageServiceDep
) -> BrandAnalysisService:
    return BrandAnalysisService(session=session, audit=AuditService(session), storage=storage)


BrandAnalysisServiceDep = Annotated[BrandAnalysisService, Depends(get_brand_analysis_service)]


@router.post(
    "/api/v1/companies/{company_id}/brand-profile/analyze",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def analyze_company_brand_profile(
    company_id: str,
    payload: AnalyzeBrandRequest,
    current_user: CurrentUser,
    service: BrandAnalysisServiceDep,
) -> BrandProfilePublic:
    profile = await service.analyze(
        BrandAnalysisScope.company, company_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)


@router.post(
    "/api/v1/products/{product_id}/brand-profile/analyze",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def analyze_product_brand_profile(
    product_id: str,
    payload: AnalyzeBrandRequest,
    current_user: CurrentUser,
    service: BrandAnalysisServiceDep,
) -> BrandProfilePublic:
    profile = await service.analyze(
        BrandAnalysisScope.product, product_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)


@router.post(
    "/api/v1/sub-products/{sub_product_id}/brand-profile/analyze",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_sub_product_access(ProductRole.product_manager))],
)
async def analyze_sub_product_brand_profile(
    sub_product_id: str,
    payload: AnalyzeBrandRequest,
    current_user: CurrentUser,
    service: BrandAnalysisServiceDep,
) -> BrandProfilePublic:
    profile = await service.analyze(
        BrandAnalysisScope.sub_product, sub_product_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)
