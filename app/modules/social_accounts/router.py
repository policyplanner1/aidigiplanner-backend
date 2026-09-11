from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse

from app.core.deps import CurrentUser, DbSession, require_product_access
from app.core.rate_limit import rate_limit_by_ip
from app.models.enums import ProductRole, SocialPlatform
from app.modules.audit.service import AuditService
from app.modules.social_accounts.oauth import SocialOAuthClient
from app.modules.social_accounts.provider import get_social_oauth_client
from app.modules.social_accounts.schemas import (
    AddSocialAccountRequest,
    FacebookTestPostResponse,
    SocialAccountPublic,
    StartSocialOAuthRequest,
    StartSocialOAuthResponse,
)
from app.modules.social_accounts.service import SocialAccountService

router = APIRouter(tags=["social-accounts"])

SocialOAuthClientDep = Annotated[SocialOAuthClient, Depends(get_social_oauth_client)]


def get_social_account_service(
    session: DbSession, oauth: SocialOAuthClientDep
) -> SocialAccountService:
    return SocialAccountService(session=session, audit=AuditService(session), oauth=oauth)


SocialAccountServiceDep = Annotated[SocialAccountService, Depends(get_social_account_service)]


def _oauth_start_response(authorize_url: str) -> StartSocialOAuthResponse:
    values = parse_qs(urlparse(authorize_url).query).get("redirect_uri") or [""]
    return StartSocialOAuthResponse(
        authorize_url=authorize_url,
        redirect_uri=values[0] or None,
    )


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


@router.post(
    "/api/v1/products/{product_id}/social-accounts/oauth/{platform}",
    response_model=StartSocialOAuthResponse,
    dependencies=[
        Depends(require_product_access()),
        Depends(rate_limit_by_ip("social-oauth-start")),
    ],
)
async def start_social_oauth(
    product_id: str,
    platform: SocialPlatform,
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    payload: StartSocialOAuthRequest | None = None,
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(product_id, current_user, platform, payload)
    return _oauth_start_response(authorize_url)


@router.get(
    "/api/v1/products/{product_id}/social-accounts/oauth/{platform}",
    response_model=StartSocialOAuthResponse,
    dependencies=[
        Depends(require_product_access()),
        Depends(rate_limit_by_ip("social-oauth-start")),
    ],
)
async def start_social_oauth_get(
    product_id: str,
    platform: SocialPlatform,
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    return_to: str | None = Query(default=None),
    return_origin: str | None = Query(default=None),
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(
        product_id,
        current_user,
        platform,
        StartSocialOAuthRequest(return_to=return_to, return_origin=return_origin),
    )
    return _oauth_start_response(authorize_url)


@router.get(
    "/api/social/instagram/connect",
    response_model=StartSocialOAuthResponse,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-start"))],
)
async def connect_instagram(
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    product_id: str = Query(...),
    return_to: str | None = Query(default=None),
    return_origin: str | None = Query(default=None),
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(
        product_id,
        current_user,
        SocialPlatform.instagram,
        StartSocialOAuthRequest(return_to=return_to, return_origin=return_origin),
    )
    return _oauth_start_response(authorize_url)


@router.get(
    "/api/social/facebook/connect",
    response_model=StartSocialOAuthResponse,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-start"))],
)
async def connect_facebook(
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    product_id: str = Query(...),
    return_to: str | None = Query(default=None),
    return_origin: str | None = Query(default=None),
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(
        product_id,
        current_user,
        SocialPlatform.facebook,
        StartSocialOAuthRequest(return_to=return_to, return_origin=return_origin),
    )
    return _oauth_start_response(authorize_url)


@router.get(
    "/api/social/youtube/connect",
    response_model=StartSocialOAuthResponse,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-start"))],
)
async def connect_youtube(
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    product_id: str = Query(...),
    return_to: str | None = Query(default=None),
    return_origin: str | None = Query(default=None),
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(
        product_id,
        current_user,
        SocialPlatform.youtube,
        StartSocialOAuthRequest(return_to=return_to, return_origin=return_origin),
    )
    return _oauth_start_response(authorize_url)


@router.post(
    "/api/v1/products/{product_id}/social-accounts/{social_account_id}/test-post",
    response_model=FacebookTestPostResponse,
    dependencies=[
        Depends(require_product_access()),
        Depends(rate_limit_by_ip("social-test-post")),
    ],
)
async def test_post_social_account(
    product_id: str,
    social_account_id: str,
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    file: UploadFile | None = File(default=None),
    caption: str | None = Form(default=None),
) -> FacebookTestPostResponse:
    image: bytes | None = None
    filename: str | None = None
    content_type: str | None = None
    if file is not None:
        image = await file.read()
        filename = file.filename
        content_type = file.content_type
    result = await service.test_facebook_post(
        product_id,
        social_account_id,
        current_user,
        image=image,
        filename=filename,
        content_type=content_type,
        caption=caption,
    )
    return FacebookTestPostResponse(
        photo_id=result.photo_id,
        post_id=result.post_id,
        permalink=result.permalink,
        caption=(caption or "").strip() or "Test post from AI Social Planner",
    )


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


@router.get(
    "/auth/callback",
    response_model=None,
    include_in_schema=True,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-callback"))],
)
async def auth0_social_callback(
    service: SocialAccountServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    return await _oauth_callback_response(
        service, code=code, state=state, error=error, error_description=error_description
    )


@router.get(
    "/api/social/instagram/callback",
    response_model=None,
    include_in_schema=True,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-callback"))],
)
async def instagram_oauth_callback(
    service: SocialAccountServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    return await _oauth_callback_response(
        service, code=code, state=state, error=error, error_description=error_description
    )


@router.get(
    "/api/social/facebook/callback",
    response_model=None,
    include_in_schema=True,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-callback"))],
)
async def facebook_oauth_callback(
    service: SocialAccountServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    return await _oauth_callback_response(
        service, code=code, state=state, error=error, error_description=error_description
    )


@router.get(
    "/api/social/youtube/callback",
    response_model=None,
    include_in_schema=True,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-callback"))],
)
async def youtube_oauth_callback(
    service: SocialAccountServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    return await _oauth_callback_response(
        service, code=code, state=state, error=error, error_description=error_description
    )


@router.get(
    "/api/social/google/connect",
    response_model=StartSocialOAuthResponse,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-start"))],
)
async def connect_google_business(
    current_user: CurrentUser,
    service: SocialAccountServiceDep,
    product_id: str = Query(...),
    return_to: str | None = Query(default=None),
    return_origin: str | None = Query(default=None),
) -> StartSocialOAuthResponse:
    authorize_url = await service.start_oauth(
        product_id,
        current_user,
        SocialPlatform.google,
        StartSocialOAuthRequest(return_to=return_to, return_origin=return_origin),
    )
    return _oauth_start_response(authorize_url)


@router.get(
    "/api/social/google/callback",
    response_model=None,
    include_in_schema=True,
    dependencies=[Depends(rate_limit_by_ip("social-oauth-callback"))],
)
async def google_business_oauth_callback(
    service: SocialAccountServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    return await _oauth_callback_response(
        service, code=code, state=state, error=error, error_description=error_description
    )


async def _oauth_callback_response(
    service: SocialAccountService,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
) -> RedirectResponse:
    result = await service.complete_oauth(
        code=code, state=state, error=error, error_description=error_description
    )
    redirect_url = service.callback_response_url(result)
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
