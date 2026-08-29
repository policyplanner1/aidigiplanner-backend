from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, DbSession, require_product_access, require_sub_product_access
from app.models.enums import ProductRole
from app.modules.audit.service import AuditService
from app.modules.sub_products.schemas import (
    CreateSubProductsRequest,
    SubProductPublic,
    UpdateSubProductRequest,
)
from app.modules.sub_products.service import SubProductService

router = APIRouter(tags=["sub-products"])


def get_sub_product_service(session: DbSession) -> SubProductService:
    return SubProductService(session=session, audit=AuditService(session))


SubProductServiceDep = Annotated[SubProductService, Depends(get_sub_product_service)]


@router.get(
    "/api/v1/products/{product_id}/sub-products",
    response_model=list[SubProductPublic],
    dependencies=[Depends(require_product_access())],
)
async def list_sub_products(
    product_id: str, service: SubProductServiceDep
) -> list[SubProductPublic]:
    sub_products = await service.list_sub_products(product_id)
    return [SubProductPublic.model_validate(sp) for sp in sub_products]


@router.post(
    "/api/v1/products/{product_id}/sub-products",
    response_model=list[SubProductPublic],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def create_sub_products(
    product_id: str,
    payload: CreateSubProductsRequest,
    current_user: CurrentUser,
    service: SubProductServiceDep,
) -> list[SubProductPublic]:
    sub_products = await service.create_sub_products(product_id, current_user, payload)
    return [SubProductPublic.model_validate(sp) for sp in sub_products]


@router.patch(
    "/api/v1/sub-products/{sub_product_id}",
    response_model=SubProductPublic,
    dependencies=[Depends(require_sub_product_access(ProductRole.product_manager))],
)
async def update_sub_product(
    sub_product_id: str,
    payload: UpdateSubProductRequest,
    current_user: CurrentUser,
    service: SubProductServiceDep,
) -> SubProductPublic:
    sub_product = await service.update_sub_product(sub_product_id, current_user, payload)
    return SubProductPublic.model_validate(sub_product)


@router.delete(
    "/api/v1/sub-products/{sub_product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_sub_product_access(ProductRole.product_manager))],
)
async def delete_sub_product(
    sub_product_id: str, current_user: CurrentUser, service: SubProductServiceDep
) -> Response:
    await service.delete_sub_product(sub_product_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
