from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import jwt

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.models.enums import SocialAccountScope, SocialPlatform

OAUTH_STATE_TYPE = "social_oauth_state"
OAUTH_STATE_TTL_MINUTES = 10
DEFAULT_OAUTH_RETURN_TO = "/app/social-accounts"
_LOCAL_SPA_ORIGINS = frozenset(
    {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://aisocialplanner.in",
        "https://www.aisocialplanner.in",
    }
)


def safe_oauth_return_to(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "://" in raw or "\\" in raw:
        return DEFAULT_OAUTH_RETURN_TO
    path = raw.split("?", 1)[0]
    if path.startswith("/app/") or path.startswith("/onboarding/"):
        return path
    return DEFAULT_OAUTH_RETURN_TO


def safe_oauth_frontend_origin(value: str | None) -> str:
    configured = (get_settings().frontend_url or "").strip().rstrip("/")
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return configured
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return configured
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return configured
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {item for item in {configured, *_LOCAL_SPA_ORIGINS} if item}
    if origin in allowed:
        return origin
    return configured


def oauth_callback_redirect_url(
    *,
    return_to: str | None,
    frontend_origin: str | None,
    params: dict[str, str],
) -> str:
    frontend = safe_oauth_frontend_origin(frontend_origin)
    path = safe_oauth_return_to(return_to)
    parsed = urlparse(f"{frontend}{path}" if frontend else path)
    query = dict(parse_qsl(parsed.query))
    query.update({key: value for key, value in params.items() if value})
    return urlunparse(parsed._replace(query=urlencode(query)))


def encode_oauth_state(
    *,
    user_id: str,
    product_id: str,
    platform: SocialPlatform,
    scope: SocialAccountScope = SocialAccountScope.product,
    sub_product_ids: list[str] | None = None,
    return_to: str | None = None,
    frontend_origin: str | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": OAUTH_STATE_TYPE,
        "product_id": product_id,
        "platform": platform.value,
        "scope": scope.value,
        "sub_product_ids": sub_product_ids or [],
        "return_to": safe_oauth_return_to(return_to),
        "frontend_origin": safe_oauth_frontend_origin(frontend_origin),
        "iat": now,
        "exp": now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_oauth_state(state: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise BadRequestError(
            "Social account connection expired or is invalid. Please try again."
        ) from exc
    if payload.get("type") != OAUTH_STATE_TYPE:
        raise BadRequestError("Social account connection expired or is invalid. Please try again.")
    return payload
