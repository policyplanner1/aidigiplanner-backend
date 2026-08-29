from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, DbSession, require_company_role, require_product_access
from app.models.company_member import CompanyMember
from app.models.enums import CompanyRole, ProductRole
from app.modules.audit.service import AuditService
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service
from app.modules.products.schemas import (
    AddProductMemberRequest,
    CreateProductRequest,
    DashboardSummary,
    InviteProductMemberRequest,
    ProductMemberPublic,
    ProductPublic,
    UpdateProductRequest,
)
from app.modules.products.service import ProductService

router = APIRouter(tags=["products"])


def get_product_service(
    session: DbSession, email: Annotated[EmailService, Depends(get_email_service)]
) -> ProductService:
    return ProductService(session=session, audit=AuditService(session), email=email)


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]


@router.get(
    "/api/v1/companies/{company_id}/products",
    response_model=list[ProductPublic],
)
async def list_company_products(
    company_id: str,
    current_user: CurrentUser,
    service: ProductServiceDep,
    membership: Annotated[CompanyMember | None, Depends(require_company_role())],
) -> list[ProductPublic]:
    full_access = membership is None or membership.role == CompanyRole.company_admin
    products = await service.list_products(
        company_id, user_id=current_user.id, full_access=full_access
    )
    return [ProductPublic.model_validate(p) for p in products]


@router.post(
    "/api/v1/companies/{company_id}/products",
    response_model=ProductPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_company_role(CompanyRole.company_admin))],
)
async def create_product(
    company_id: str,
    payload: CreateProductRequest,
    current_user: CurrentUser,
    service: ProductServiceDep,
) -> ProductPublic:
    product = await service.create_product(company_id, current_user, payload)
    return ProductPublic.model_validate(product)


@router.patch(
    "/api/v1/products/{product_id}",
    response_model=ProductPublic,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def update_product(
    product_id: str,
    payload: UpdateProductRequest,
    current_user: CurrentUser,
    service: ProductServiceDep,
) -> ProductPublic:
    product = await service.update_product(product_id, current_user, payload)
    return ProductPublic.model_validate(product)


@router.delete(
    "/api/v1/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def delete_product(
    product_id: str, current_user: CurrentUser, service: ProductServiceDep
) -> Response:
    await service.delete_product(product_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/products/{product_id}/dashboard",
    response_model=DashboardSummary,
    dependencies=[Depends(require_product_access())],
)
async def get_product_dashboard(
    product_id: str, service: ProductServiceDep
) -> DashboardSummary:
    return await service.get_dashboard(product_id)


@router.get(
    "/api/v1/products/{product_id}/members",
    response_model=list[ProductMemberPublic],
    dependencies=[Depends(require_product_access())],
)
async def list_product_members(
    product_id: str, service: ProductServiceDep
) -> list[ProductMemberPublic]:
    return await service.list_members(product_id)


@router.post(
    "/api/v1/products/{product_id}/members",
    response_model=ProductMemberPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def add_product_member(
    product_id: str,
    payload: AddProductMemberRequest,
    current_user: CurrentUser,
    service: ProductServiceDep,
) -> ProductMemberPublic:
    return await service.add_member(product_id, current_user, payload)


@router.post(
    "/api/v1/products/{product_id}/invitations",
    response_model=ProductMemberPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def invite_product_member(
    product_id: str,
    payload: InviteProductMemberRequest,
    current_user: CurrentUser,
    service: ProductServiceDep,
) -> ProductMemberPublic:
    return await service.invite_member(product_id, current_user, payload)


@router.delete(
    "/api/v1/products/{product_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def remove_product_member(
    product_id: str, member_id: str, current_user: CurrentUser, service: ProductServiceDep
) -> Response:
    await service.remove_member(product_id, member_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
