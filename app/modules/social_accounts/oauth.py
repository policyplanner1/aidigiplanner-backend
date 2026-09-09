from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import httpx
import jwt

from app.core.config import Settings, get_settings
from app.core.exceptions import BadRequestError
from app.core.logging_setup import get_logger
from app.db.mixins import utcnow
from app.models.enums import SocialPlatform

logger = get_logger(__name__)

GRAPH_FB = "https://graph.facebook.com/v21.0"
GRAPH_IG = "https://graph.instagram.com/v21.0"
_GRAPH_VERSIONS = ("v23.0", "v22.0", "v21.0", "v18.0", "v16.0")
HTTP_TIMEOUT = httpx.Timeout(20.0)
# Meta rejects these on current Facebook Login / Instagram Graph apps.
_INVALID_META_CONNECTION_SCOPES = frozenset(
    {
        "instagram_content_publish",
        "publish_pages",
        "manage_pages",
        "publish_actions",
    }
)


@dataclass(frozen=True)
class FacebookPagesLookup:
    pages: list[dict[str, Any]]
    graph_error: str | None = None
    declined: tuple[str, ...] = ()
    granted: tuple[str, ...] = ()


_PAGE_FIELDS = (
    "id,name,access_token,"
    "instagram_business_account{id,username,name,profile_picture_url}"
)
_PAGE_FIELDS_BASIC = "id,name,access_token,tasks"


@dataclass(frozen=True)
class ConnectedSocialProfile:
    platform: SocialPlatform
    handle: str
    profile_url: str | None
    external_account_id: str
    access_token: str
    refresh_token: str | None
    token_expires_at: datetime | None
    auth0_user_id: str
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    extra_profiles: tuple[ConnectedSocialProfile, ...] = ()


class SocialOAuthClient(Protocol):
    def is_configured(self) -> bool: ...

    def is_configured_for(self, platform: SocialPlatform) -> bool: ...

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str: ...

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile: ...


class UnconfiguredSocialOAuthClient:
    def is_configured(self) -> bool:
        return False

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return False

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        raise BadRequestError(
            "Social account OAuth is not configured.", code="oauth_not_configured"
        )

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        raise BadRequestError(
            "Social account OAuth is not configured.", code="oauth_not_configured"
        )


class Auth0SocialOAuthClient:
    """Auth0 as the OAuth broker: authorize → Meta login → code → tokens → IG account."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._mgmt_token: str | None = None
        self._mgmt_token_expires_at: datetime | None = None

    def is_configured(self) -> bool:
        return bool(
            self._settings.auth0_domain
            and self._settings.auth0_client_id
            and self._settings.auth0_client_secret
            and self._settings.auth0_callback_url
        )

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return platform is SocialPlatform.instagram and self.is_configured()

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        if platform is not SocialPlatform.instagram:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        connection = (self._settings.auth0_instagram_connection or "").strip()
        params = {
            "response_type": "code",
            "client_id": self._settings.auth0_client_id,
            "redirect_uri": self._settings.auth0_callback_url,
            "scope": "openid profile email",
            "state": state,
        }
        if connection:
            params["connection"] = connection
        connection_scope = _meta_connection_scope(self._settings.auth0_instagram_connection_scope)
        if connection_scope:
            params["connection_scope"] = connection_scope
        return f"https://{self._settings.auth0_domain}/authorize?{urlencode(params)}"

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        try:
            return await self._complete_authorization(platform=platform, code=code)
        except httpx.HTTPError as exc:
            logger.info("oauth_transport_error", error=str(exc))
            raise BadRequestError(
                "Could not reach Auth0 or Meta. Check AUTH0_DOMAIN and your network."
            ) from exc
        except jwt.PyJWTError as exc:
            raise BadRequestError("Auth0 returned an invalid login token.") from exc

    async def _complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        tokens = await self._exchange_code(code)
        auth0_user_id = _sub_from_id_token(tokens["id_token"], self._settings)
        identity = await self._fetch_identity(auth0_user_id)
        access_token = str(identity.get("access_token") or "")
        if not access_token:
            raise BadRequestError(
                "Auth0 did not return an Instagram access token. In Auth0: "
                "Applications → APIs → Auth0 Management API → authorize this "
                "app with read:users and read:user_idp_tokens.",
                code="oauth_missing_idp_token",
            )
        provider = str(identity.get("provider") or "instagram")
        refresh_token = identity.get("refresh_token")
        expires_in = identity.get("expires_in")
        expires_at = _expires_at(expires_in)
        access_token, expires_at = await self._maybe_extend_token(
            access_token, expires_at, provider
        )

        profile = await self._fetch_instagram_account(access_token, provider)
        if profile.get("access_token"):
            access_token = str(profile["access_token"])
            expires_at = None

        username = profile["username"]
        handle = f"@{username.lstrip('@')}"
        return ConnectedSocialProfile(
            platform=platform,
            handle=handle,
            profile_url=f"https://instagram.com/{username.lstrip('@')}",
            external_account_id=profile["id"],
            access_token=access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            token_expires_at=expires_at,
            auth0_user_id=auth0_user_id,
            provider_metadata={
                "provider": provider,
                "page_id": profile.get("page_id"),
                "account_type": profile.get("account_type"),
            },
        )

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._settings.auth0_client_id,
            "client_secret": self._settings.auth0_client_secret,
            "code": code,
            "redirect_uri": self._settings.auth0_callback_url,
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                f"https://{self._settings.auth0_domain}/oauth/token",
                json=payload,
            )
        data = _json_or_error(response, "Could not complete Instagram login.")
        if not data.get("id_token"):
            raise BadRequestError("Auth0 did not return an ID token.")
        return data

    async def _management_token(self) -> str:
        now = utcnow()
        if (
            self._mgmt_token
            and self._mgmt_token_expires_at
            and self._mgmt_token_expires_at > now + timedelta(minutes=2)
        ):
            return self._mgmt_token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self._settings.auth0_client_id,
            "client_secret": self._settings.auth0_client_secret,
            "audience": f"https://{self._settings.auth0_domain}/api/v2/",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                f"https://{self._settings.auth0_domain}/oauth/token",
                json=payload,
            )
        data = _json_or_error(
            response,
            "Could not reach Auth0 Management API. Authorize this application "
            "for the Management API with read:users and read:user_idp_tokens.",
        )
        token = data.get("access_token")
        if not token:
            raise BadRequestError(
                "Could not reach Auth0 Management API. Authorize this application "
                "for the Management API with read:users and read:user_idp_tokens.",
                code="oauth_management_api",
            )
        self._mgmt_token = str(token)
        self._mgmt_token_expires_at = now + timedelta(seconds=int(data.get("expires_in") or 3600))
        return self._mgmt_token

    async def _fetch_identity(self, auth0_user_id: str) -> dict[str, Any]:
        mgmt = await self._management_token()
        encoded_id = quote(auth0_user_id, safe="")
        url = f"https://{self._settings.auth0_domain}/api/v2/users/{encoded_id}"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {mgmt}"})
        profile = _json_or_error(response, "Could not load the Instagram identity from Auth0.")
        raw_identities = profile.get("identities") or []
        if not isinstance(raw_identities, list) or not raw_identities:
            raise BadRequestError("Auth0 returned no social identity for this login.")
        identities: list[dict[str, Any]] = [
            item for item in raw_identities if isinstance(item, dict)
        ]
        if not identities:
            raise BadRequestError("Auth0 returned no social identity for this login.")
        preferred = ("instagram", "facebook", "oauth2")
        for provider in preferred:
            for identity in identities:
                if identity.get("provider") == provider and identity.get("access_token"):
                    return identity
        return identities[0]

    async def _fetch_instagram_account(
        self, access_token: str, provider: str
    ) -> dict[str, Any]:
        if provider == "facebook":
            account = await fetch_facebook_linked_instagram(access_token)
            if account is not None:
                return account
            account = await fetch_instagram_graph_account(access_token)
            if account is not None:
                return account
        else:
            account = await fetch_instagram_graph_account(access_token)
            if account is not None:
                return account
            account = await fetch_facebook_linked_instagram(access_token)
            if account is not None:
                return account
        raise BadRequestError(
            "No Instagram professional account was found. Use a Business or "
            "Creator account and grant the requested permissions.",
            code="instagram_account_not_found",
        )

    async def _from_instagram_graph(self, access_token: str) -> dict[str, Any] | None:
        return await fetch_instagram_graph_account(access_token)

    async def _from_facebook_pages(self, access_token: str) -> dict[str, Any] | None:
        return await fetch_facebook_linked_instagram(access_token)

    async def _maybe_extend_token(
        self,
        access_token: str,
        expires_at: datetime | None,
        provider: str,
    ) -> tuple[str, datetime | None]:
        return await extend_meta_token(self._settings, access_token, expires_at, provider)


def _sub_from_id_token(id_token: str, settings: Settings) -> str:
    # Token was fetched from Auth0's token endpoint with the client secret,
    # so we trust issuance and only check iss/aud/sub. Disable PyJWT's aud
    # check because we validate audience ourselves below.
    try:
        claims = jwt.decode(
            id_token,
            options={"verify_signature": False, "verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise BadRequestError("Auth0 returned an invalid login token.") from exc
    issuer = str(claims.get("iss") or "").rstrip("/")
    expected = f"https://{settings.auth0_domain}".rstrip("/")
    if issuer != expected:
        raise BadRequestError("Auth0 ID token issuer mismatch.")
    aud = claims.get("aud")
    if aud != settings.auth0_client_id and (
        not isinstance(aud, list) or settings.auth0_client_id not in aud
    ):
        raise BadRequestError("Auth0 ID token audience mismatch.")
    sub = claims.get("sub")
    if not sub:
        raise BadRequestError("Auth0 ID token is missing the user id.")
    return str(sub)


def _expires_at(expires_in: Any) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return utcnow() + timedelta(seconds=seconds)


def _json_or_error(response: httpx.Response, message: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except ValueError:
        body = {}
    if response.status_code >= 400:
        logger.info(
            "oauth_http_error",
            status_code=response.status_code,
            path=str(response.request.url.path),
            auth0_error=body.get("error"),
        )
        detail = str(
            body.get("error_description")
            or body.get("error_message")
            or body.get("error")
            or ""
        ).strip()
        if detail:
            raise BadRequestError(_clean_oauth_detail(detail, message))
        raise BadRequestError(message)
    if not body:
        raise BadRequestError(message)
    return body


def _clean_oauth_detail(detail: str, fallback: str) -> str:
    text = " ".join(detail.split())
    if not text:
        return fallback
    lowered = text.lower()
    if (
        "client credentials" in lowered
        or "unauthorized_client" in lowered
        or "management api" in lowered
    ):
        return (
            "Auth0 Management API is not authorized for this app. Prefer direct Meta "
            "login by setting META_APP_ID and META_APP_SECRET, or in Auth0 open "
            "Applications → APIs → Auth0 Management API and enable this application "
            "with read:users and read:user_idp_tokens."
        )
    if "callback" in lowered or "redirect" in lowered:
        return (
            "Auth0 callback URL mismatch. Add AUTH0_CALLBACK_URL exactly to the "
            "app's Allowed Callback URLs."
        )
    if "connection" in lowered:
        return (
            "Auth0 social connection is missing or disabled. Enable Facebook "
            "(recommended) or Instagram under Authentication → Social, then set "
            "AUTH0_INSTAGRAM_CONNECTION to that connection name."
        )
    return text[:300]


def _meta_connection_scope(raw: str | None) -> str:
    scopes: list[str] = []
    seen: set[str] = set()
    for item in (raw or "").split(","):
        scope = item.strip()
        if not scope or scope in seen or scope in _INVALID_META_CONNECTION_SCOPES:
            continue
        seen.add(scope)
        scopes.append(scope)
    return ",".join(scopes)


async def fetch_instagram_graph_account(access_token: str) -> dict[str, Any] | None:
    params = {
        "fields": "user_id,id,username,name,account_type,profile_picture_url",
        "access_token": access_token,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(f"{GRAPH_IG}/me", params=params)
    if response.status_code >= 400:
        logger.info("instagram_graph_me_failed", status_code=response.status_code)
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    account_id = data.get("user_id") or data.get("id")
    username = data.get("username")
    if not account_id or not username:
        return None
    return {
        "id": str(account_id),
        "username": str(username),
        "account_type": data.get("account_type"),
    }


def _graph_error_text(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        text = str(error.get("error_user_msg") or error.get("message") or "").strip()
        return text or None
    return None


def _appsecret_proof(access_token: str, app_secret: str) -> str:
    return hmac_new(app_secret.encode("utf-8"), access_token.encode("utf-8"), sha256).hexdigest()


async def _graph_get(
    url: str,
    access_token: str,
    params: dict[str, str],
    *,
    app_secret: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    query = {**params, "access_token": access_token}
    if app_secret:
        query["appsecret_proof"] = _appsecret_proof(access_token, app_secret)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url, params=query)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code >= 400:
        return None, _graph_error_text(response) or f"Meta returned HTTP {response.status_code}."
    if not isinstance(payload, dict):
        return None, "Meta returned an invalid response."
    return payload, None


def _pages_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw_pages = payload.get("data")
    if not isinstance(raw_pages, list):
        raw_pages = None
    if raw_pages is None and isinstance(payload.get("accounts"), dict):
        nested = payload["accounts"].get("data")
        raw_pages = nested if isinstance(nested, list) else []
    pages: list[dict[str, Any]] = []
    for page in raw_pages or []:
        parsed = _page_from_object(page)
        if parsed:
            pages.append(parsed)
    return pages


def _page_from_object(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload.get("id"):
        return None
    if payload.get("error"):
        return None
    ig_raw = payload.get("instagram_business_account") or {}
    instagram: dict[str, str] | None = None
    if isinstance(ig_raw, dict) and ig_raw.get("id") and ig_raw.get("username"):
        instagram = {
            "id": str(ig_raw["id"]),
            "username": str(ig_raw["username"]),
        }
    return {
        "id": str(payload["id"]),
        "name": str(payload.get("name") or "Facebook Page"),
        "access_token": payload.get("access_token"),
        "instagram": instagram,
    }


_PAGE_TARGET_SCOPES = frozenset(
    {
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "pages_manage_metadata",
        "pages_manage_engagement",
        "pages_read_user_content",
        "business_management",
    }
)


def _page_ids_from_debug_token(payload: dict[str, Any] | None) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    ids: list[str] = []
    for item in data.get("granular_scopes") or []:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "").strip()
        if scope and scope not in _PAGE_TARGET_SCOPES:
            continue
        for target in item.get("target_ids") or []:
            page_id = str(target).strip()
            if page_id:
                ids.append(page_id)
    return list(dict.fromkeys(ids))


async def _meta_permission_names(
    access_token: str, *, app_secret: str = ""
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    payload, _error = await _graph_get(
        f"{GRAPH_FB}/me/permissions", access_token, {}, app_secret=app_secret
    )
    declined: list[str] = []
    granted: list[str] = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict) or not item.get("permission"):
            continue
        name = str(item["permission"])
        if item.get("status") == "granted":
            granted.append(name)
        elif item.get("status") == "declined":
            declined.append(name)
    return tuple(declined), tuple(granted)


async def _collect_account_pages(
    access_token: str, *, app_secret: str = ""
) -> tuple[list[dict[str, Any]], str | None]:
    last_error: str | None = None
    found: dict[str, dict[str, Any]] = {}
    field_sets = (_PAGE_FIELDS_BASIC, _PAGE_FIELDS)
    queries = (
        ("me/accounts", {"limit": "100"}),
        ("me", {}),
    )
    for version in _GRAPH_VERSIONS:
        for path, base_params in queries:
            for fields in field_sets:
                params = {
                    **base_params,
                    "fields": fields if path == "me/accounts" else f"accounts{{{fields}}}",
                }
                payload, error = await _graph_get(
                    f"https://graph.facebook.com/{version}/{path}",
                    access_token,
                    params,
                    app_secret=app_secret,
                )
                if error:
                    last_error = error
                    continue
                for page in _pages_from_payload(payload):
                    found[page["id"]] = page
            if found:
                return list(found.values()), None
    return list(found.values()), last_error


async def lookup_facebook_pages(
    access_token: str, settings: Settings | None = None
) -> FacebookPagesLookup:
    settings = settings or get_settings()
    app_secret = settings.meta_app_secret
    declined, granted = await _meta_permission_names(access_token, app_secret=app_secret)
    pages_by_id: dict[str, dict[str, Any]] = {}
    graph_error: str | None = None

    accounts, accounts_error = await _collect_account_pages(
        access_token, app_secret=app_secret
    )
    if accounts_error and not accounts:
        graph_error = accounts_error
        logger.info("facebook_pages_failed", error=accounts_error)
    for page in accounts:
        pages_by_id[page["id"]] = page

    businesses, businesses_error = await _graph_get(
        f"{GRAPH_FB}/me/businesses",
        access_token,
        {"fields": "id,name"},
        app_secret=app_secret,
    )
    if businesses_error and graph_error is None and not pages_by_id:
        logger.info("facebook_businesses_failed", error=businesses_error)
    for business in (businesses or {}).get("data") or []:
        if not isinstance(business, dict) or not business.get("id"):
            continue
        business_id = str(business["id"])
        for edge in ("owned_pages", "client_pages"):
            for fields in (_PAGE_FIELDS_BASIC, _PAGE_FIELDS):
                payload, _error = await _graph_get(
                    f"{GRAPH_FB}/{business_id}/{edge}",
                    access_token,
                    {"fields": fields},
                    app_secret=app_secret,
                )
                for page in _pages_from_payload(payload):
                    pages_by_id[page["id"]] = page

    if settings.meta_app_id and app_secret:
        debug_payload, debug_error = await _graph_get(
            f"{GRAPH_FB}/debug_token",
            f"{settings.meta_app_id}|{app_secret}",
            {"input_token": access_token},
        )
        if debug_error and not pages_by_id:
            logger.info("facebook_debug_token_failed", error=debug_error)
        for page_id in _page_ids_from_debug_token(debug_payload):
            if page_id in pages_by_id and pages_by_id[page_id].get("access_token"):
                continue
            payload, _error = await _graph_get(
                f"{GRAPH_FB}/{page_id}",
                access_token,
                {"fields": _PAGE_FIELDS_BASIC},
                app_secret=app_secret,
            )
            parsed = _page_from_object(payload)
            if parsed:
                existing = pages_by_id.get(page_id) or {}
                pages_by_id[page_id] = {
                    **existing,
                    **parsed,
                    "access_token": parsed.get("access_token") or existing.get("access_token"),
                }

    for page_id, page in list(pages_by_id.items()):
        if page.get("access_token"):
            continue
        payload, _error = await _graph_get(
            f"{GRAPH_FB}/{page_id}",
            access_token,
            {"fields": "id,name,access_token"},
            app_secret=app_secret,
        )
        token = (payload or {}).get("access_token") if isinstance(payload, dict) else None
        if token:
            pages_by_id[page_id] = {**page, "access_token": token, "name": payload.get("name") or page.get("name")}

    logger.info(
        "facebook_pages_lookup",
        page_count=len(pages_by_id),
        granted=list(granted),
        declined=list(declined),
    )
    return FacebookPagesLookup(
        pages=list(pages_by_id.values()),
        graph_error=graph_error if not pages_by_id else None,
        declined=declined,
        granted=granted,
    )


async def fetch_facebook_pages(access_token: str) -> list[dict[str, Any]]:
    return (await lookup_facebook_pages(access_token)).pages


async def fetch_facebook_linked_instagram(access_token: str) -> dict[str, Any] | None:
    for page in await fetch_facebook_pages(access_token):
        ig = page.get("instagram") or {}
        if ig.get("id") and ig.get("username"):
            return {
                "id": str(ig["id"]),
                "username": str(ig["username"]),
                "page_id": str(page.get("id") or ""),
                "page_name": page.get("name"),
                "account_type": "BUSINESS",
                "access_token": page.get("access_token"),
            }
    return None


async def fetch_meta_user(access_token: str) -> dict[str, Any] | None:
    params = {"fields": "id,name", "access_token": access_token}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(f"{GRAPH_FB}/me", params=params)
    if response.status_code >= 400:
        logger.info("meta_me_failed", status_code=response.status_code)
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return {"id": str(data["id"]), "name": str(data.get("name") or "")}


async def extend_meta_token(
    settings: Settings,
    access_token: str,
    expires_at: datetime | None,
    provider: str,
) -> tuple[str, datetime | None]:
    secret = settings.meta_app_secret
    if not secret:
        return access_token, expires_at
    if provider == "facebook":
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": secret,
            "fb_exchange_token": access_token,
        }
        url = f"{GRAPH_FB}/oauth/access_token"
    else:
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": secret,
            "access_token": access_token,
        }
        url = "https://graph.instagram.com/access_token"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400:
        logger.info("long_lived_token_exchange_failed", status_code=response.status_code)
        return access_token, expires_at
    try:
        data = response.json()
    except ValueError:
        return access_token, expires_at
    if not isinstance(data, dict):
        return access_token, expires_at
    long_lived = data.get("access_token")
    if not long_lived:
        return access_token, expires_at
    return str(long_lived), _expires_at(data.get("expires_in")) or expires_at
