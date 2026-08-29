from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.core.deps import (
    CurrentUser,
    DbSession,
    require_company_role,
    require_product_access,
    require_sub_product_access,
)
from app.core.exceptions import NotFoundError
from app.models.enums import BrandAnalysisScope, CompanyRole, ProductRole
from app.models.sub_product import SubProduct
from app.modules.audit.service import AuditService
from app.modules.brand_profiles.resolution import resolve_effective_brand_profile
from app.modules.brand_profiles.schemas import BrandProfilePublic, UpsertBrandProfileRequest
from app.modules.brand_profiles.service import BrandProfileService
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service

router = APIRouter(tags=["brand-profiles"])

StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]


def get_brand_profile_service(
    session: DbSession, storage: StorageServiceDep
) -> BrandProfileService:
    return BrandProfileService(session=session, audit=AuditService(session), storage=storage)


BrandProfileServiceDep = Annotated[BrandProfileService, Depends(get_brand_profile_service)]


# --- Product scope (unchanged paths from before the scope generalization) ---


@router.get(
    "/api/v1/products/{product_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access())],
)
async def get_product_brand_profile(
    product_id: str, service: BrandProfileServiceDep
) -> BrandProfilePublic:
    profile = await service.get_brand_profile(BrandAnalysisScope.product, product_id)
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/products/{product_id}/brand-profile/effective",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access())],
)
async def get_product_effective_brand_profile(
    product_id: str, session: DbSession
) -> BrandProfilePublic:
    """The brand profile actually applied to this product -- its own if
    `branding_mode` is `separate_brand` and one has been set, otherwise its
    company's (Phase 8/14's "resolved" view, as opposed to the raw
    `/brand-profile` above which 404s if this product has no row of its
    own)."""
    profile = await resolve_effective_brand_profile(session, product_id=product_id)
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/products/{product_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def upsert_product_brand_profile(
    product_id: str,
    payload: UpsertBrandProfileRequest,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
) -> BrandProfilePublic:
    profile = await service.upsert_brand_profile(
        BrandAnalysisScope.product, product_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/products/{product_id}/brand-profile/avatar",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def upload_product_brand_avatar(
    product_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.product,
        product_id,
        current_user,
        slot="avatar",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/products/{product_id}/brand-profile/avatar",
    dependencies=[Depends(require_product_access())],
)
async def download_product_brand_avatar(
    product_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.product, product_id, slot="avatar"
    )
    return Response(content=data, media_type=content_type)


@router.put(
    "/api/v1/products/{product_id}/brand-profile/logo",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def upload_product_brand_logo(
    product_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.product,
        product_id,
        current_user,
        slot="logo",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/products/{product_id}/brand-profile/logo",
    dependencies=[Depends(require_product_access())],
)
async def download_product_brand_logo(
    product_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.product, product_id, slot="logo"
    )
    return Response(content=data, media_type=content_type)


@router.put(
    "/api/v1/products/{product_id}/brand-profile/logo-dark",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def upload_product_brand_dark_logo(
    product_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.product,
        product_id,
        current_user,
        slot="logo-dark",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/products/{product_id}/brand-profile/logo-dark",
    dependencies=[Depends(require_product_access())],
)
async def download_product_brand_dark_logo(
    product_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.product, product_id, slot="logo-dark"
    )
    return Response(content=data, media_type=content_type)


@router.put(
    "/api/v1/products/{product_id}/brand-profile/icon",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def upload_product_brand_icon(
    product_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.product,
        product_id,
        current_user,
        slot="icon",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/products/{product_id}/brand-profile/icon",
    dependencies=[Depends(require_product_access())],
)
async def download_product_brand_icon(
    product_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.product, product_id, slot="icon"
    )
    return Response(content=data, media_type=content_type)


# --- Company scope (Phase 4A single-brand company profile) ---
# Only a "logo" image slot -- Phase 4B's group profile and Phase 6's
# single-brand review only ever show one logo at the company level; the
# dark-logo/icon/avatar slots exist for product-level creative rendering
# only (see the product-scope routes above).


@router.get(
    "/api/v1/companies/{company_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_company_role())],
)
async def get_company_brand_profile(
    company_id: str, service: BrandProfileServiceDep
) -> BrandProfilePublic:
    profile = await service.get_brand_profile(BrandAnalysisScope.company, company_id)
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/companies/{company_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def upsert_company_brand_profile(
    company_id: str,
    payload: UpsertBrandProfileRequest,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
) -> BrandProfilePublic:
    profile = await service.upsert_brand_profile(
        BrandAnalysisScope.company, company_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/companies/{company_id}/brand-profile/logo",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def upload_company_brand_logo(
    company_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.company,
        company_id,
        current_user,
        slot="logo",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/companies/{company_id}/brand-profile/logo",
    dependencies=[Depends(require_company_role())],
)
async def download_company_brand_logo(
    company_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.company, company_id, slot="logo"
    )
    return Response(content=data, media_type=content_type)


# --- Sub-product scope (Phase 9's "separate_brand" opt-out) ---
# Only a "logo" slot, same reasoning as company scope above.


@router.get(
    "/api/v1/sub-products/{sub_product_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_sub_product_access())],
)
async def get_sub_product_brand_profile(
    sub_product_id: str, service: BrandProfileServiceDep
) -> BrandProfilePublic:
    profile = await service.get_brand_profile(BrandAnalysisScope.sub_product, sub_product_id)
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/sub-products/{sub_product_id}/brand-profile/effective",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_sub_product_access())],
)
async def get_sub_product_effective_brand_profile(
    sub_product_id: str, session: DbSession
) -> BrandProfilePublic:
    sub_product = await session.get(SubProduct, sub_product_id)
    if sub_product is None:
        raise NotFoundError("Sub-product not found.")
    profile = await resolve_effective_brand_profile(
        session, product_id=sub_product.product_id, sub_product_id=sub_product_id
    )
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/sub-products/{sub_product_id}/brand-profile",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_sub_product_access(ProductRole.product_manager))],
)
async def upsert_sub_product_brand_profile(
    sub_product_id: str,
    payload: UpsertBrandProfileRequest,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
) -> BrandProfilePublic:
    profile = await service.upsert_brand_profile(
        BrandAnalysisScope.sub_product, sub_product_id, current_user, payload
    )
    return BrandProfilePublic.model_validate(profile)


@router.put(
    "/api/v1/sub-products/{sub_product_id}/brand-profile/logo",
    response_model=BrandProfilePublic,
    dependencies=[Depends(require_sub_product_access(ProductRole.product_manager))],
)
async def upload_sub_product_brand_logo(
    sub_product_id: str,
    current_user: CurrentUser,
    service: BrandProfileServiceDep,
    file: Annotated[UploadFile, File()],
) -> BrandProfilePublic:
    data = await file.read()
    profile = await service.upload_image(
        BrandAnalysisScope.sub_product,
        sub_product_id,
        current_user,
        slot="logo",
        data=data,
        content_type=file.content_type or "",
    )
    return BrandProfilePublic.model_validate(profile)


@router.get(
    "/api/v1/sub-products/{sub_product_id}/brand-profile/logo",
    dependencies=[Depends(require_sub_product_access())],
)
async def download_sub_product_brand_logo(
    sub_product_id: str, service: BrandProfileServiceDep
) -> Response:
    data, content_type = await service.get_image_bytes(
        BrandAnalysisScope.sub_product, sub_product_id, slot="logo"
    )
    return Response(content=data, media_type=content_type)
