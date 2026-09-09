from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.models.enums import SocialAccountScope, SocialPlatform

OAUTH_STATE_TYPE = "social_oauth_state"
OAUTH_STATE_TTL_MINUTES = 10
DEFAULT_OAUTH_RETURN_TO = "/app/social-accounts"


def safe_oauth_return_to(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "://" in raw or "\\" in raw:
        return DEFAULT_OAUTH_RETURN_TO
    path = raw.split("?", 1)[0]
    if path.startswith("/app/") or path.startswith("/onboarding/"):
        return path
    return DEFAULT_OAUTH_RETURN_TO


def encode_oauth_state(
    *,
    user_id: str,
    product_id: str,
    platform: SocialPlatform,
    scope: SocialAccountScope = SocialAccountScope.product,
    sub_product_ids: list[str] | None = None,
    return_to: str | None = None,
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
