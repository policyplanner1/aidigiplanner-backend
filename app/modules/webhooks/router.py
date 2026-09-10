from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ForbiddenError
from app.core.logging_setup import get_logger
from app.modules.social_accounts.router import SocialAccountServiceDep, _oauth_callback_response

logger = get_logger(__name__)

router = APIRouter(tags=["webhooks"])


@router.get("/api/webhooks/instagram", response_model=None)
async def instagram_webhook_or_oauth(
    service: SocialAccountServiceDep,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> Response:
    if hub_mode == "subscribe":
        settings = get_settings()
        expected = (settings.meta_webhook_verify_token or "").strip()
        if not expected or hub_verify_token != expected or hub_challenge is None:
            raise ForbiddenError("Instagram webhook verify token does not match.")
        return PlainTextResponse(hub_challenge, status_code=status.HTTP_200_OK)

    if code or state or error:
        return await _oauth_callback_response(
            service, code=code, state=state, error=error, error_description=error_description
        )

    return PlainTextResponse("Instagram webhook is ready.", status_code=status.HTTP_200_OK)


@router.post("/api/webhooks/instagram")
async def instagram_webhook_event(request: Request) -> dict[str, bool]:
    raw = await request.body()
    _verify_meta_signature(raw, request.headers.get("x-hub-signature-256", ""))
    payload: Any = {}
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
        if isinstance(parsed, dict):
            payload = parsed
    except (UnicodeDecodeError, ValueError):
        payload = {}
    object_name = payload.get("object") if isinstance(payload, dict) else None
    entries = payload.get("entry") if isinstance(payload, dict) else None
    logger.info(
        "instagram_webhook_event",
        object=object_name,
        entries=len(entries) if isinstance(entries, list) else 0,
    )
    return {"success": True}


def _verify_meta_signature(raw: bytes, header: str) -> None:
    secret = (get_settings().meta_app_secret or "").encode("utf-8")
    if not secret:
        raise BadRequestError("META_APP_SECRET is required to accept Instagram webhooks.")
    prefix = "sha256="
    if not header.startswith(prefix):
        raise ForbiddenError("Instagram webhook signature is missing.")
    expected = prefix + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header):
        raise ForbiddenError("Instagram webhook signature is invalid.")
