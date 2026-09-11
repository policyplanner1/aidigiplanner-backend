from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import BadRequestError
from app.core.logging_setup import get_logger
from app.models.enums import SocialPlatform
from app.modules.social_accounts.oauth import (
    GRAPH_FB,
    HTTP_TIMEOUT,
    ConnectedSocialProfile,
    FacebookPagesLookup,
    _INVALID_META_CONNECTION_SCOPES,
    _json_or_error,
    _meta_connection_scope,
    extend_meta_token,
    lookup_facebook_pages,
    fetch_instagram_graph_account,
    fetch_meta_user,
)

logger = get_logger(__name__)

FACEBOOK_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
INSTAGRAM_AUTH_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_META_PLATFORMS = frozenset({SocialPlatform.instagram, SocialPlatform.facebook})
_FACEBOOK_LOGIN_SCOPES = frozenset(
    {
        "instagram_basic",
        "pages_show_list",
        "pages_read_engagement",
        "business_management",
        "pages_manage_posts",
        "pages_manage_metadata",
    }
)
_DEFAULT_INSTAGRAM_LOGIN_SCOPE = "instagram_business_basic"


class MetaSocialOAuthClient:
    """Instagram Login for Instagram, Facebook Login for Pages."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(
            self._settings.meta_app_id
            and self._settings.meta_app_secret
            and self._settings.meta_redirect_uri
        )

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return platform in _META_PLATFORMS and self.is_configured()

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        if platform not in _META_PLATFORMS:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        if platform is SocialPlatform.instagram:
            scope = _instagram_login_scope(self._settings.meta_oauth_scope)
            params = {
                "client_id": self._instagram_app_id(),
                "redirect_uri": self._redirect_uri(platform),
                "state": state,
                "response_type": "code",
                "scope": scope,
                "enable_fb_login": "0",
                "force_authentication": "1",
            }
            logger.info(
                "instagram_oauth_authorize",
                redirect_uri=params["redirect_uri"],
                client_id=params["client_id"],
            )
            return f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"

        scope = self._scope_for(platform)
        params = {
            "client_id": self._settings.meta_app_id,
            "redirect_uri": self._redirect_uri(platform),
            "state": state,
            "response_type": "code",
            "display": "page",
            "override_default_response_type": "true",
            "auth_type": "rerequest",
        }
        config_id = (self._settings.meta_login_config_id or "").strip()
        if config_id:
            params["config_id"] = config_id
        elif scope:
            params["scope"] = scope
        return f"{FACEBOOK_AUTH_URL}?{urlencode(params)}"

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        try:
            return await self._complete_authorization(platform=platform, code=code)
        except httpx.HTTPError as exc:
            logger.info("meta_oauth_transport_error", error=str(exc))
            raise BadRequestError(
                "Could not reach Meta. Check META_APP_ID and your network."
            ) from exc

    async def _complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        if platform is SocialPlatform.instagram:
            tokens = await self._exchange_instagram_code(code)
            user_token = str(tokens.get("access_token") or "")
            if not user_token:
                raise BadRequestError("Instagram did not return an access token.")
            user_token, _expires_at = await extend_meta_token(
                self._settings, user_token, None, "instagram"
            )
            ig = await fetch_instagram_graph_account(user_token)
            if not ig:
                raise BadRequestError(
                    "No Instagram professional account was found. Use a Business or "
                    "Creator Instagram account and grant Instagram access.",
                    code="instagram_account_not_found",
                )
            user_id = str(tokens.get("user_id") or ig["id"])
            return self._instagram_profile(
                ig,
                access_token=user_token,
                auth0_user_id=f"instagram|{user_id}",
                page_id="",
            )

        tokens = await self._exchange_code(platform=platform, code=code)
        user_token = str(tokens.get("access_token") or "")
        if not user_token:
            raise BadRequestError("Meta did not return an access token.")
        user_token, expires_at = await extend_meta_token(
            self._settings, user_token, None, "facebook"
        )
        meta_user = await fetch_meta_user(user_token)
        auth0_user_id = f"meta|{meta_user['id']}" if meta_user else ""
        pages_lookup = await lookup_facebook_pages(user_token, self._settings)
        pages = [page for page in pages_lookup.pages if page.get("access_token")]
        first_page = pages[0] if pages else None
        ig_graph = await fetch_instagram_graph_account(user_token)

        if platform is SocialPlatform.facebook:
            if first_page is None:
                nameless = [page for page in pages_lookup.pages if page.get("id")]
                if nameless:
                    names = ", ".join(str(page.get("name") or page["id"]) for page in nameless[:3])
                    raise BadRequestError(
                        f"Facebook found Page {names} but did not return a Page access token. "
                        "Disconnect and Connect Facebook again, tick that Page in the dialog, "
                        "and allow Pages posting (pages_manage_posts)."
                    )
                raise _facebook_page_missing_error(
                    pages_lookup, instagram_found=bool(ig_graph)
                )
            return self._facebook_profile(
                first_page,
                auth0_user_id=auth0_user_id,
                expires_at=expires_at,
                fallback_token=user_token,
            )

        raise BadRequestError(
            f"OAuth is not available for {platform.value} yet.",
            code="oauth_platform_unsupported",
        )

    async def _exchange_code(self, *, platform: SocialPlatform, code: str) -> dict[str, Any]:
        params = {
            "client_id": self._settings.meta_app_id,
            "client_secret": self._settings.meta_app_secret,
            "redirect_uri": self._redirect_uri(platform),
            "code": code,
            "return_scopes": "true",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(f"{GRAPH_FB}/oauth/access_token", params=params)
        return _json_or_error(response, "Could not complete Meta login.")

    async def _exchange_instagram_code(self, code: str) -> dict[str, Any]:
        payload = {
            "client_id": self._instagram_app_id(),
            "client_secret": self._instagram_app_secret(),
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri(SocialPlatform.instagram),
            "code": code,
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(INSTAGRAM_TOKEN_URL, data=payload)
        data = _json_or_error(response, "Could not complete Instagram login.")
        nested = data.get("data")
        if not data.get("access_token") and isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                return first
        return data

    def _instagram_app_id(self) -> str:
        return (self._settings.instagram_app_id or self._settings.meta_app_id).strip()

    def _instagram_app_secret(self) -> str:
        return (self._settings.instagram_app_secret or self._settings.meta_app_secret).strip()

    def _redirect_uri(self, platform: SocialPlatform) -> str:
        if platform is SocialPlatform.facebook:
            return self._settings.meta_facebook_redirect_uri.strip()
        return self._settings.meta_redirect_uri.strip()

    def _scope_for(self, platform: SocialPlatform) -> str:
        raw = (
            self._settings.meta_facebook_oauth_scope
            if platform is SocialPlatform.facebook
            else self._settings.meta_oauth_scope
        )
        return _meta_connection_scope(raw)

    def _instagram_profile(
        self,
        ig: dict[str, Any],
        *,
        access_token: str,
        auth0_user_id: str,
        page_id: str,
        extra_profiles: tuple[ConnectedSocialProfile, ...] = (),
    ) -> ConnectedSocialProfile:
        username = str(ig.get("username") or "").lstrip("@")
        return ConnectedSocialProfile(
            platform=SocialPlatform.instagram,
            handle=f"@{username}",
            profile_url=f"https://instagram.com/{username}",
            external_account_id=str(ig["id"]),
            access_token=access_token,
            refresh_token=None,
            token_expires_at=None,
            auth0_user_id=auth0_user_id,
            provider_metadata={
                "provider": "meta",
                "page_id": page_id or None,
                "account_type": ig.get("account_type") or "BUSINESS",
            },
            extra_profiles=extra_profiles,
        )

    def _facebook_profile(
        self,
        page: dict[str, Any],
        *,
        auth0_user_id: str,
        expires_at: Any,
        fallback_token: str,
        extra_profiles: tuple[ConnectedSocialProfile, ...] = (),
    ) -> ConnectedSocialProfile:
        name = str(page.get("name") or "Facebook Page")
        page_id = str(page["id"])
        page_token = str(page.get("access_token") or "").strip()
        if not page_token:
            raise BadRequestError(
                "Facebook did not return a Page access token. Disconnect and Connect "
                "Facebook again, pick a Page you manage, and allow Pages posting."
            )
        return ConnectedSocialProfile(
            platform=SocialPlatform.facebook,
            handle=name,
            profile_url=f"https://facebook.com/{page_id}",
            external_account_id=page_id,
            access_token=page_token,
            refresh_token=None,
            token_expires_at=expires_at,
            auth0_user_id=auth0_user_id,
            provider_metadata={"provider": "meta", "page_id": page_id},
            extra_profiles=extra_profiles,
        )


def _instagram_login_scope(raw: str | None) -> str:
    scopes: list[str] = []
    seen: set[str] = set()
    for item in (raw or "").split(","):
        scope = item.strip()
        if (
            not scope
            or scope in seen
            or scope in _INVALID_META_CONNECTION_SCOPES
            or scope in _FACEBOOK_LOGIN_SCOPES
        ):
            continue
        seen.add(scope)
        scopes.append(scope)
    return ",".join(scopes) or _DEFAULT_INSTAGRAM_LOGIN_SCOPE


def _facebook_page_missing_error(
    lookup: FacebookPagesLookup, *, instagram_found: bool
) -> BadRequestError:
    declined = {item.lower() for item in lookup.declined}
    granted = {item.lower() for item in lookup.granted}
    if "pages_show_list" in declined or "pages_read_engagement" in declined:
        return BadRequestError(
            "Facebook Page access was declined. Connect again, allow Pages, and "
            "select the Page you manage. A personal Facebook profile is not a Page.",
            code="facebook_page_not_found",
        )
    if granted and "pages_show_list" not in granted:
        return BadRequestError(
            "Meta did not grant Pages access. Connect again and in the Facebook "
            "dialog choose the Page you manage (not only your profile).",
            code="facebook_page_not_found",
        )
    if granted and "pages_show_list" in granted:
        return BadRequestError(
            "Facebook granted Pages access but no Page was selected. Connect again, "
            "open the Pages list in the Facebook dialog, and tick the Page you manage "
            "(not only Continue as your profile).",
            code="facebook_page_not_found",
        )
    if lookup.graph_error:
        return BadRequestError(
            f"Could not load Facebook Pages from Meta. {lookup.graph_error}",
            code="facebook_page_not_found",
        )
    if instagram_found:
        return BadRequestError(
            "This login has Instagram, but no Facebook Page. Use Connect Instagram "
            "for that account, or create a Facebook Page, then connect Facebook and "
            "select that Page in the Meta dialog.",
            code="facebook_page_not_found",
        )
    return BadRequestError(
        "No Facebook Page was found. Facebook needs a Page you manage, not a "
        "personal profile. Connect again, allow Pages, and select your Page. If the "
        "Meta app is in Development mode, add this Facebook user as an Admin or "
        "Tester. If the Page is in Meta Business Suite, this user must be a Page admin.",
        code="facebook_page_not_found",
    )
