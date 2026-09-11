from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.core.logging_setup import get_logger
from app.db.mixins import utcnow
from app.models.company_member import CompanyMember
from app.models.enums import (
    CompanyRole,
    SocialAccountScope,
    SocialConnectionMethod,
    SocialPlatform,
)
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.social_account import SocialAccount
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.social_accounts.oauth import ConnectedSocialProfile, SocialOAuthClient
from app.modules.social_accounts.publish import (
    FacebookPhotoPost,
    normalize_test_image,
    publish_facebook_photo,
)
from app.modules.social_accounts.schemas import AddSocialAccountRequest, StartSocialOAuthRequest
from app.modules.social_accounts.state import (
    decode_oauth_state,
    encode_oauth_state,
    oauth_callback_redirect_url,
    safe_oauth_frontend_origin,
    safe_oauth_return_to,
)
from app.modules.social_accounts.tokens import decrypt_secret, encrypt_secret

logger = get_logger(__name__)

OAUTH_PLATFORMS = {
    SocialPlatform.instagram,
    SocialPlatform.facebook,
    SocialPlatform.youtube,
    SocialPlatform.google,
}


@dataclass
class SocialOAuthCallbackResult:
    ok: bool
    product_id: str | None
    platform: str
    handle: str | None = None
    message: str | None = None
    return_to: str | None = None
    frontend_origin: str | None = None


class SocialAccountService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        oauth: SocialOAuthClient | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._oauth = oauth

    async def list_accounts(self, product_id: str) -> list[SocialAccount]:
        result = await self._session.scalars(
            select(SocialAccount)
            .where(SocialAccount.product_id == product_id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at)
        )
        return list(result)

    async def add_account(
        self, product_id: str, actor: User, payload: AddSocialAccountRequest
    ) -> SocialAccount:
        existing = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.product_id == product_id,
                SocialAccount.platform == payload.platform,
                SocialAccount.handle == payload.handle,
                SocialAccount.deleted_at.is_(None),
            )
        )
        if existing is not None:
            raise ConflictError("This social account is already connected to the product.")

        account = SocialAccount(
            product_id=product_id,
            platform=payload.platform,
            handle=payload.handle,
            profile_url=payload.profile_url,
            scope=payload.scope,
            sub_product_ids=payload.sub_product_ids,
            added_by=actor.id,
        )
        self._session.add(account)
        await self._audit.log(
            action="social_account.added",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="social_account",
            resource_id=account.id,
            metadata={"platform": payload.platform.value, "handle": payload.handle},
        )
        await self._session.commit()
        return account

    async def remove_account(self, product_id: str, social_account_id: str, actor: User) -> None:
        account = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.id == social_account_id,
                SocialAccount.product_id == product_id,
                SocialAccount.deleted_at.is_(None),
            )
        )
        if account is None:
            raise NotFoundError("Social account not found.")

        account.deleted_at = utcnow()
        await self._audit.log(
            action="social_account.removed",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="social_account",
            resource_id=social_account_id,
        )
        await self._session.commit()

    async def test_facebook_post(
        self,
        product_id: str,
        social_account_id: str,
        actor: User,
        *,
        image: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        caption: str | None = None,
    ) -> FacebookPhotoPost:
        await self._require_product(product_id, actor)
        account = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.id == social_account_id,
                SocialAccount.product_id == product_id,
                SocialAccount.deleted_at.is_(None),
            )
        )
        if account is None:
            raise NotFoundError("Social account not found.")
        if account.platform is not SocialPlatform.facebook:
            raise BadRequestError("Test posting is only available for Facebook Pages right now.")
        if account.connection_method is not SocialConnectionMethod.oauth:
            raise BadRequestError("Connect Facebook with OAuth before sending a test post.")
        if not account.access_token_encrypted or not account.external_account_id:
            raise BadRequestError("This Facebook Page has no stored token. Reconnect the account.")

        logo, logo_name, logo_type = normalize_test_image(
            content=image, filename=filename, content_type=content_type
        )
        message = (caption or "").strip() or "Test post from AI Social Planner"
        result = await publish_facebook_photo(
            page_id=account.external_account_id,
            page_access_token=decrypt_secret(account.access_token_encrypted),
            image=logo,
            filename=logo_name,
            content_type=logo_type,
            caption=message,
        )
        await self._audit.log(
            action="social_account.test_posted",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="social_account",
            resource_id=account.id,
            metadata={
                "platform": SocialPlatform.facebook.value,
                "photo_id": result.photo_id,
                "post_id": result.post_id,
            },
        )
        await self._session.commit()
        return result

    async def start_oauth(
        self,
        product_id: str,
        actor: User,
        platform: SocialPlatform,
        payload: StartSocialOAuthRequest | None = None,
    ) -> str:
        if platform not in OAUTH_PLATFORMS:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        if self._oauth is None or not self._oauth.is_configured_for(platform):
            if platform is SocialPlatform.youtube:
                raise BadRequestError(
                    "YouTube OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                    "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI.",
                    code="oauth_not_configured",
                )
            if platform is SocialPlatform.google:
                raise BadRequestError(
                    "Google Business Profile OAuth is not configured. Set "
                    "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and "
                    "GOOGLE_BUSINESS_REDIRECT_URI.",
                    code="oauth_not_configured",
                )
            if platform is SocialPlatform.facebook:
                raise BadRequestError(
                    "Facebook OAuth is not configured. Set META_APP_ID, "
                    "META_APP_SECRET, and META_REDIRECT_URI.",
                    code="oauth_not_configured",
                )
            raise BadRequestError(
                "Instagram OAuth is not configured. Set META_APP_ID and "
                "META_APP_SECRET (preferred), or AUTH0_DOMAIN, AUTH0_CLIENT_ID, "
                "AUTH0_CLIENT_SECRET, and AUTH0_CALLBACK_URL.",
                code="oauth_not_configured",
            )
        payload = payload or StartSocialOAuthRequest()
        await self._require_product(product_id, actor)
        state = encode_oauth_state(
            user_id=actor.id,
            product_id=product_id,
            platform=platform,
            scope=payload.scope,
            sub_product_ids=payload.sub_product_ids,
            return_to=payload.return_to,
            frontend_origin=payload.return_origin,
        )
        return self._oauth.build_authorize_url(platform=platform, state=state)

    async def complete_oauth(
        self,
        *,
        code: str | None,
        state: str | None,
        error: str | None,
        error_description: str | None = None,
    ) -> SocialOAuthCallbackResult:
        claims: dict[str, object] | None = None
        if state:
            try:
                claims = decode_oauth_state(state)
            except BadRequestError:
                claims = None

        product_id = str(claims["product_id"]) if claims else None
        platform = str(claims["platform"]) if claims else SocialPlatform.instagram.value
        label = _oauth_label(platform)
        return_to = safe_oauth_return_to(
            str(claims["return_to"]) if claims and claims.get("return_to") else None
        )
        frontend_origin = safe_oauth_frontend_origin(
            str(claims["frontend_origin"]) if claims and claims.get("frontend_origin") else None
        )

        if error:
            reason = error_description or error
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=_user_facing_oauth_error(reason, platform),
                return_to=return_to,
                frontend_origin=frontend_origin,
            )
        if not code or not claims:
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=f"{label} connection expired or is invalid. Please try again.",
                return_to=return_to,
                frontend_origin=frontend_origin,
            )
        try:
            requested_platform = SocialPlatform(platform)
        except ValueError:
            requested_platform = SocialPlatform.instagram
        if self._oauth is None or not self._oauth.is_configured_for(requested_platform):
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message="Social account OAuth is not configured.",
                return_to=return_to,
                frontend_origin=frontend_origin,
            )

        try:
            actor = await self._session.get(User, str(claims["sub"]))
            if actor is None or actor.deleted_at is not None:
                raise BadRequestError(
                    f"{label} connection expired or is invalid. Please try again."
                )
            connected_product_id = str(claims["product_id"])
            product = await self._require_product(connected_product_id, actor)
            profile = await self._oauth.complete_authorization(
                platform=SocialPlatform(str(claims["platform"])), code=code
            )
            raw_sub_products = claims.get("sub_product_ids") or []
            sub_product_ids = (
                [str(item) for item in raw_sub_products]
                if isinstance(raw_sub_products, list)
                else []
            )
            scope = SocialAccountScope(
                str(claims.get("scope") or SocialAccountScope.product.value)
            )
            account = await self._upsert_oauth_account(
                product=product,
                actor=actor,
                profile=profile,
                scope=scope,
                sub_product_ids=sub_product_ids,
            )
            for extra in profile.extra_profiles:
                await self._upsert_oauth_account(
                    product=product,
                    actor=actor,
                    profile=extra,
                    scope=scope,
                    sub_product_ids=sub_product_ids,
                )
        except AppError as exc:
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=exc.message,
                return_to=return_to,
                frontend_origin=frontend_origin,
            )
        except OperationalError as exc:
            logger.exception("social_oauth_schema_error")
            detail = str(getattr(exc, "orig", None) or exc).strip()
            message = "Database is missing social OAuth columns. Run alembic upgrade head."
            if get_settings().env == "development" and detail:
                message = f"{message} ({detail[:240]})"
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=message,
                return_to=return_to,
                frontend_origin=frontend_origin,
            )
        except IntegrityError:
            logger.exception("social_oauth_conflict")
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=f"This {label} account is already linked to the product.",
                return_to=return_to,
                frontend_origin=frontend_origin,
            )
        except Exception as exc:
            logger.exception("social_oauth_callback_failed")
            detail = str(exc).strip() or exc.__class__.__name__
            if get_settings().env == "development":
                message = (
                    f"Could not connect {label} ({exc.__class__.__name__}: {detail[:240]})"
                )
            else:
                message = f"Could not connect {label}. Please try again."
            return SocialOAuthCallbackResult(
                ok=False,
                product_id=product_id,
                platform=platform,
                message=message,
                return_to=return_to,
                frontend_origin=frontend_origin,
            )

        return SocialOAuthCallbackResult(
            ok=True,
            product_id=product_id,
            platform=profile.platform.value,
            handle=account.handle,
            return_to=return_to,
            frontend_origin=frontend_origin,
        )

    def callback_response_url(self, result: SocialOAuthCallbackResult) -> str:
        params: dict[str, str] = {
            "platform": result.platform,
            "status": "connected" if result.ok else "error",
        }
        if result.product_id:
            params["product_id"] = result.product_id
        if result.handle:
            params["handle"] = result.handle
        if result.message:
            params["message"] = result.message
        return oauth_callback_redirect_url(
            return_to=result.return_to,
            frontend_origin=result.frontend_origin,
            params=params,
        )

    async def _upsert_oauth_account(
        self,
        *,
        product: Product,
        actor: User,
        profile: ConnectedSocialProfile,
        scope: SocialAccountScope,
        sub_product_ids: list[str],
    ) -> SocialAccount:
        account = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.product_id == product.id,
                SocialAccount.platform == profile.platform,
                SocialAccount.external_account_id == profile.external_account_id,
            )
        )
        if account is None:
            account = await self._session.scalar(
                select(SocialAccount).where(
                    SocialAccount.product_id == product.id,
                    SocialAccount.platform == profile.platform,
                    SocialAccount.handle == profile.handle,
                )
            )

        created = account is None
        if account is None:
            account = SocialAccount(
                product_id=product.id,
                platform=profile.platform,
                handle=profile.handle,
                added_by=actor.id,
            )
            self._session.add(account)

        account.handle = profile.handle
        account.profile_url = profile.profile_url
        account.scope = scope
        account.sub_product_ids = sub_product_ids
        account.connection_method = SocialConnectionMethod.oauth
        account.external_account_id = profile.external_account_id
        account.access_token_encrypted = encrypt_secret(profile.access_token)
        account.refresh_token_encrypted = (
            encrypt_secret(profile.refresh_token) if profile.refresh_token else None
        )
        account.token_expires_at = profile.token_expires_at
        account.auth0_user_id = profile.auth0_user_id or None
        account.provider_metadata = profile.provider_metadata
        account.deleted_at = None

        await self._audit.log(
            action="social_account.connected" if not created else "social_account.added",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product.id,
            resource_type="social_account",
            resource_id=account.id,
            metadata={
                "platform": profile.platform.value,
                "handle": profile.handle,
                "external_account_id": profile.external_account_id,
                "connection_method": SocialConnectionMethod.oauth.value,
            },
        )
        await self._session.commit()
        return account

    async def _require_product(self, product_id: str, actor: User) -> Product:
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found")
        if actor.is_super_admin:
            return product

        company_membership = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == product.company_id,
                CompanyMember.user_id == actor.id,
            )
        )
        if company_membership is not None and company_membership.role == CompanyRole.company_admin:
            return product

        product_membership = await self._session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product_id,
                ProductMember.user_id == actor.id,
            )
        )
        if product_membership is None:
            raise NotFoundError("Product not found")
        return product


def _oauth_label(platform: str) -> str:
    if platform == SocialPlatform.youtube.value:
        return "YouTube"
    if platform == SocialPlatform.google.value:
        return "Google Business Profile"
    if platform == SocialPlatform.instagram.value:
        return "Instagram"
    return platform.replace("_", " ").title()


def _user_facing_oauth_error(reason: str, platform: str = "instagram") -> str:
    label = _oauth_label(platform)
    lowered = reason.lower()
    if "access_denied" in lowered or "denied" in lowered:
        return f"{label} connection was cancelled."
    if platform == SocialPlatform.instagram.value and "connection" in lowered:
        return (
            "Meta login could not complete. Confirm META_REDIRECT_URI is listed "
            "under Valid OAuth Redirect URIs in the Meta app, or enable the "
            "Auth0 Facebook connection if you are still using AUTH0_DOMAIN."
        )
    cleaned = " ".join(reason.split())
    if cleaned and cleaned not in {"invalid_request", "server_error", "unauthorized"}:
        return cleaned[:300]
    return f"Could not connect {label}. Please try again."
