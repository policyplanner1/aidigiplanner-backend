from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, DbSession, require_product_access
from app.models.enums import ProductRole
from app.modules.audit.service import AuditService
from app.modules.social_accounts.schemas import AddSocialAccountRequest, SocialAccountPublic
from app.modules.social_accounts.service import SocialAccountService

router = APIRouter(tags=["social-accounts"])


def get_social_account_service(session: DbSession) -> SocialAccountService:
    return SocialAccountService(session=session, audit=AuditService(session))


SocialAccountServiceDep = Annotated[SocialAccountService, Depends(get_social_account_service)]


@router.get(
    "/api/v1/products/{product_id}/social-accounts",
    response_model=list[SocialAccountPublic],
    dependencies=[Depends(require_product_access())],
)
async def list_social_accounts(
    product_id: str, service: SocialAccountServiceDep
) -> list[SocialAccountPublic]:
    accounts = await service.list_accounts(product_id)
    return [SocialAccountPublic.model_validate(a) for a in accounts]


@router.post(
    "/api/v1/products/{product_id}/social-accounts",
    response_model=SocialAccountPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_product_access())],
)
async def add_social_account(
    product_id: str,
    payload: AddSocialAccountRequest,
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
) -> SocialAccountPublic:
    account = await service.add_account(product_id, current_user, payload)
    return SocialAccountPublic.model_validate(account)


@router.delete(
    "/api/v1/products/{product_id}/social-accounts/{social_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_product_access(ProductRole.product_manager))],
)
async def remove_social_account(
    product_id: str,
    social_account_id: str,
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
) -> Response:
    await service.remove_account(product_id, social_account_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
