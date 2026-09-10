from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import BadRequestError
from app.core.logging_setup import get_logger
from app.db.mixins import utcnow
from app.models.enums import SocialPlatform
from app.modules.social_accounts.oauth import HTTP_TIMEOUT, ConnectedSocialProfile

logger = get_logger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
GBP_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
GBP_LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"

YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/userinfo.email "
    "openid"
)
GBP_SCOPES = (
    "https://www.googleapis.com/auth/business.manage "
    "https://www.googleapis.com/auth/userinfo.email "
    "openid"
)
GBP_LOCATION_READ_MASK = "name,title,storefrontAddress,websiteUri,metadata"


class GoogleYouTubeOAuthClient:
    """Google OAuth for connecting a YouTube channel. Tokens stay on the backend."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(
            self._settings.google_client_id
            and self._settings.google_client_secret
            and self._settings.google_redirect_uri
        )

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return platform is SocialPlatform.youtube and self.is_configured()

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        if platform is not SocialPlatform.youtube:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        if not self.is_configured():
            raise BadRequestError(
                "YouTube OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI.",
                code="oauth_not_configured",
            )
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": YOUTUBE_SCOPES,
            "access_type": "offline",
            "prompt": "select_account consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        if platform is not SocialPlatform.youtube:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        try:
            tokens = await self._exchange_code(code)
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                raise BadRequestError("Google did not return an access token.")
            channel = await self._fetch_channel(access_token)
        except httpx.HTTPError as exc:
            logger.info("youtube_oauth_transport_error", error=str(exc))
            raise BadRequestError(
                "Could not reach Google. Check GOOGLE_CLIENT_ID and your network."
            ) from exc

        refresh_token = tokens.get("refresh_token")
        custom_url = str(channel.get("custom_url") or "").lstrip("@")
        title = str(channel["title"])
        handle = f"@{custom_url}" if custom_url else title
        channel_id = str(channel["id"])
        return ConnectedSocialProfile(
            platform=SocialPlatform.youtube,
            handle=handle,
            profile_url=(
                f"https://youtube.com/@{custom_url}"
                if custom_url
                else f"https://youtube.com/channel/{channel_id}"
            ),
            external_account_id=channel_id,
            access_token=access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            token_expires_at=_expires_at(tokens.get("expires_in")),
            auth0_user_id="",
            provider_metadata={
                "provider": "google",
                "title": title,
                "custom_url": custom_url or None,
                "thumbnail": channel.get("thumbnail"),
                "scopes": tokens.get("scope"),
            },
        )

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, datetime | None]:
        payload = {
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
        data = _google_json(response, "Could not refresh the YouTube access token.")
        token = str(data.get("access_token") or "")
        if not token:
            raise BadRequestError("Google did not return a refreshed access token.")
        return token, _expires_at(data.get("expires_in"))

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        payload = {
            "code": code,
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret,
            "redirect_uri": self._settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
        return _google_json(response, "Could not complete YouTube login.")

    async def _fetch_channel(self, access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                YOUTUBE_CHANNELS_URL,
                params={"part": "snippet,contentDetails", "mine": "true"},
                headers=headers,
            )
            data = _google_json(
                response,
                "Could not load the YouTube channel. Enable the YouTube Data API "
                "for this Google Cloud project.",
            )
            items = data.get("items") or []
            if not items:
                managed = await client.get(
                    YOUTUBE_CHANNELS_URL,
                    params={"part": "snippet,contentDetails", "managedByMe": "true"},
                    headers=headers,
                )
                if managed.status_code < 400:
                    managed_data = managed.json()
                    if isinstance(managed_data, dict):
                        items = managed_data.get("items") or []
                else:
                    logger.info(
                        "youtube_managed_by_me_failed",
                        status_code=managed.status_code,
                    )
            email = await _google_account_email(client, headers)

        if not items:
            who = f" ({email})" if email else ""
            raise BadRequestError(
                f"This Google account{who} has no YouTube channel the API can see. "
                "On the Google screen pick the same account that owns the channel in "
                "YouTube Studio. If it is a Brand Account, choose that Brand Account, "
                "not only your Gmail login.",
                code="youtube_channel_not_found",
            )
        item = items[0] if isinstance(items[0], dict) else {}
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        title = str(snippet.get("title") or "").strip()
        channel_id = str(item.get("id") or "").strip()
        if not title or not channel_id:
            raise BadRequestError("YouTube did not return a channel name.")
        raw_thumbs = snippet.get("thumbnails")
        thumbnails = raw_thumbs if isinstance(raw_thumbs, dict) else {}
        raw_default = thumbnails.get("default")
        default_thumb = raw_default if isinstance(raw_default, dict) else {}
        return {
            "id": channel_id,
            "title": title,
            "custom_url": snippet.get("customUrl"),
            "thumbnail": default_thumb.get("url"),
        }


class GoogleBusinessOAuthClient:
    """Google OAuth for connecting a Business Profile location. Tokens stay on the backend."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(
            self._settings.google_client_id
            and self._settings.google_client_secret
            and self._settings.google_business_redirect_uri
        )

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return platform is SocialPlatform.google and self.is_configured()

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        if platform is not SocialPlatform.google:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        if not self.is_configured():
            raise BadRequestError(
                "Google Business Profile OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_BUSINESS_REDIRECT_URI.",
                code="oauth_not_configured",
            )
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_business_redirect_uri,
            "response_type": "code",
            "scope": GBP_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        if platform is not SocialPlatform.google:
            raise BadRequestError(
                f"OAuth is not available for {platform.value} yet.",
                code="oauth_platform_unsupported",
            )
        try:
            tokens = await self._exchange_code(code)
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                raise BadRequestError("Google did not return an access token.")
            location = await self._fetch_location(access_token)
        except httpx.HTTPError as exc:
            logger.info("gbp_oauth_transport_error", error=str(exc))
            raise BadRequestError(
                "Could not reach Google. Check GOOGLE_CLIENT_ID and your network."
            ) from exc

        refresh_token = tokens.get("refresh_token")
        title = str(location["title"])
        location_id = str(location["id"])
        return ConnectedSocialProfile(
            platform=SocialPlatform.google,
            handle=title,
            profile_url=location.get("profile_url"),
            external_account_id=location_id[:64],
            access_token=access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            token_expires_at=_expires_at(tokens.get("expires_in")),
            auth0_user_id="",
            provider_metadata={
                "provider": "google_business",
                "title": title,
                "account_name": location.get("account_name"),
                "location_name": location.get("location_name"),
                "address": location.get("address"),
                "maps_uri": location.get("maps_uri"),
                "scopes": tokens.get("scope"),
                "other_locations": location.get("other_locations") or [],
            },
        )

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        payload = {
            "code": code,
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret,
            "redirect_uri": self._settings.google_business_redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
        return _google_json(response, "Could not complete Google Business Profile login.")

    async def _fetch_location(self, access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            accounts_resp = await client.get(GBP_ACCOUNTS_URL, headers=headers)
        accounts_data = _google_json(
            accounts_resp,
            "Could not load Google Business accounts. Enable the Account "
            "Management API for this Google Cloud project.",
        )
        accounts = [
            item for item in (accounts_data.get("accounts") or []) if isinstance(item, dict)
        ]
        if not accounts:
            raise BadRequestError(
                "This Google account has no Business Profile. Create a profile "
                "at business.google.com, then connect again.",
                code="google_business_account_not_found",
            )

        locations: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            for account in accounts:
                account_name = str(account.get("name") or "").strip()
                if not account_name:
                    continue
                response = await client.get(
                    GBP_LOCATIONS_URL.format(account=account_name),
                    params={"readMask": GBP_LOCATION_READ_MASK, "pageSize": 100},
                    headers=headers,
                )
                data = _google_json(
                    response,
                    "Could not load Business Profile locations. Enable the "
                    "Business Information API for this Google Cloud project.",
                )
                for item in data.get("locations") or []:
                    if isinstance(item, dict):
                        locations.append({**item, "_account_name": account_name})

        parsed = [_parse_gbp_location(item) for item in locations]
        parsed = [item for item in parsed if item]
        if not parsed:
            raise BadRequestError(
                "No Google Business location was found. Add a location in "
                "Business Profile, then connect again.",
                code="google_business_location_not_found",
            )
        primary = parsed[0]
        primary["other_locations"] = [
            {"id": item["id"], "title": item["title"]} for item in parsed[1:]
        ]
        return primary


class CompositeSocialOAuthClient:
    """Routes Instagram/Facebook through Meta (or Auth0 fallback) and Google networks through Google."""

    def __init__(
        self,
        *,
        instagram: Any,
        youtube: Any,
        google: Any,
    ) -> None:
        self._instagram = instagram
        self._youtube = youtube
        self._google = google

    def is_configured(self) -> bool:
        return (
            self._instagram.is_configured()
            or self._youtube.is_configured()
            or self._google.is_configured()
        )

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return self._client_for(platform).is_configured_for(platform)

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        return self._client_for(platform).build_authorize_url(platform=platform, state=state)

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        return await self._client_for(platform).complete_authorization(
            platform=platform, code=code
        )

    def _client_for(self, platform: SocialPlatform) -> Any:
        if platform is SocialPlatform.youtube:
            return self._youtube
        if platform is SocialPlatform.google:
            return self._google
        if platform in {SocialPlatform.instagram, SocialPlatform.facebook}:
            return self._instagram
        raise BadRequestError(
            f"OAuth is not available for {platform.value} yet.",
            code="oauth_platform_unsupported",
        )


def _expires_at(expires_in: Any) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return utcnow() + timedelta(seconds=seconds)


def _google_json(response: httpx.Response, message: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except ValueError:
        body = {}
    if response.status_code >= 400:
        logger.info(
            "google_oauth_http_error",
            status_code=response.status_code,
            google_error=body.get("error"),
        )
        detail = str(body.get("error_description") or body.get("error") or "").strip()
        if isinstance(body.get("error"), dict):
            err = body["error"]
            detail = str(err.get("message") or err.get("status") or detail)
        if detail:
            return _raise_google_detail(detail, message)
        raise BadRequestError(message)
    if not body:
        raise BadRequestError(message)
    return body


def _raise_google_detail(detail: str, fallback: str) -> dict[str, Any]:
    text = " ".join(detail.split())[:300]
    lowered = text.lower()
    if "redirect_uri" in lowered or "redirect uri" in lowered:
        raise BadRequestError(
            "Google redirect URI mismatch. Add GOOGLE_REDIRECT_URI exactly to "
            "the OAuth client's Authorized redirect URIs."
        )
    if "access_denied" in lowered:
        raise BadRequestError("Google connection was cancelled.")
    raise BadRequestError(text or fallback)


async def _google_account_email(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    try:
        response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo", headers=headers
        )
    except httpx.HTTPError:
        return ""
    if response.status_code >= 400:
        return ""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    return str(body.get("email") or "").strip()


def _parse_gbp_location(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    resource = str(item.get("name") or "").strip()
    if not title or not resource:
        return None
    location_id = resource.rsplit("/", 1)[-1] if "/" in resource else resource
    raw_meta = item.get("metadata")
    metadata = raw_meta if isinstance(raw_meta, dict) else {}
    raw_address = item.get("storefrontAddress")
    address = raw_address if isinstance(raw_address, dict) else {}
    street_lines = address.get("addressLines")
    street = ""
    if isinstance(street_lines, list) and street_lines:
        street = str(street_lines[0])
    formatted_address = ", ".join(
        part
        for part in (
            street,
            str(address.get("locality") or ""),
            str(address.get("administrativeArea") or ""),
        )
        if part
    )
    maps_uri = str(metadata.get("mapsUri") or "").strip()
    website = str(item.get("websiteUri") or "").strip()
    return {
        "id": location_id[:64],
        "title": title,
        "account_name": item.get("_account_name"),
        "location_name": resource,
        "address": formatted_address or None,
        "maps_uri": maps_uri or None,
        "profile_url": maps_uri or website or f"https://business.google.com/n/{location_id}",
    }
