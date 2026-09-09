from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.core.logging_setup import get_logger
from app.modules.social_accounts.oauth import GRAPH_FB, HTTP_TIMEOUT, _appsecret_proof, lookup_facebook_pages

logger = get_logger(__name__)

MAX_TEST_IMAGE_BYTES = 8 * 1024 * 1024
_ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class FacebookPhotoPost:
    photo_id: str
    post_id: str | None
    permalink: str | None


def default_test_logo_png() -> bytes:
    return _solid_png(96, 96, (232, 90, 45))


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def normalize_test_image(
    *,
    content: bytes | None,
    filename: str | None,
    content_type: str | None,
) -> tuple[bytes, str, str]:
    if content:
        if len(content) > MAX_TEST_IMAGE_BYTES:
            raise BadRequestError("The test image is too large. Use a file under 8 MB.")
        media_type = (content_type or "").split(";")[0].strip().lower() or "image/png"
        if media_type not in _ALLOWED_IMAGE_TYPES:
            raise BadRequestError("Use a PNG, JPEG, WebP, or GIF for the Facebook test post.")
        name = filename or f"logo{_ALLOWED_IMAGE_TYPES[media_type]}"
        return content, name, media_type
    return default_test_logo_png(), "logo.png", "image/png"


async def publish_facebook_photo(
    *,
    page_id: str,
    page_access_token: str,
    image: bytes,
    filename: str,
    content_type: str,
    caption: str,
) -> FacebookPhotoPost:
    page_id, token = await _resolve_page_target(page_id, page_access_token)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{GRAPH_FB}/{page_id}/photos",
            data={
                "message": caption,
                "published": "true",
                "access_token": token,
            },
            files={"source": (filename, image, content_type)},
        )
    payload = _graph_payload(response)
    photo_id = str(payload.get("id") or "")
    post_id = str(payload.get("post_id") or "") or None
    if not photo_id:
        raise BadRequestError("Facebook accepted the upload but did not return a photo id.")
    permalink = (
        f"https://www.facebook.com/{post_id}" if post_id else f"https://www.facebook.com/{photo_id}"
    )
    return FacebookPhotoPost(photo_id=photo_id, post_id=post_id, permalink=permalink)


async def _resolve_page_target(page_id: str, token: str) -> tuple[str, str]:
    info = await _debug_token_data(token)
    token_type = str(info.get("type") or "").upper()
    if token_type == "PAGE":
        return page_id, token

    lookup = await lookup_facebook_pages(token)
    matching = next(
        (
            page
            for page in lookup.pages
            if str(page.get("id")) == str(page_id) and page.get("access_token")
        ),
        None,
    )
    chosen = matching or next(
        (page for page in lookup.pages if page.get("access_token")),
        None,
    )
    if chosen and chosen.get("access_token"):
        resolved_id = str(chosen["id"])
        if resolved_id != str(page_id):
            logger.info(
                "facebook_publish_using_listed_page",
                stored_id=page_id,
                page_id=resolved_id,
            )
        return resolved_id, str(chosen["access_token"])

    exchanged = await _page_access_token(page_id, token)
    if exchanged and exchanged != token:
        return page_id, exchanged

    raise BadRequestError(
        "Facebook publishing needs a Page access token. User tokens cannot post "
        "(publish_actions is deprecated). Disconnect Facebook, Connect again, "
        "and grant access to a Page you manage."
    )


async def _debug_token_data(token: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.meta_app_id or not settings.meta_app_secret:
        return {}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{GRAPH_FB}/debug_token",
                params={
                    "input_token": token,
                    "access_token": f"{settings.meta_app_id}|{settings.meta_app_secret}",
                },
            )
    except httpx.HTTPError:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


async def _page_access_token(page_id: str, token: str) -> str:
    settings = get_settings()
    params: dict[str, str] = {"fields": "id,access_token", "access_token": token}
    if settings.meta_app_secret:
        params["appsecret_proof"] = _appsecret_proof(token, settings.meta_app_secret)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(f"{GRAPH_FB}/{page_id}", params=params)
    except httpx.HTTPError:
        return token
    if response.status_code >= 400:
        return token
    try:
        payload = response.json()
    except ValueError:
        return token
    if not isinstance(payload, dict):
        return token
    page_token = str(payload.get("access_token") or "").strip()
    return page_token or token


def _graph_payload(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except ValueError:
        body = {}
    if response.status_code < 400:
        return body
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    detail = str(error.get("error_user_msg") or error.get("message") or "").strip()
    logger.info(
        "facebook_photo_upload_failed",
        status_code=response.status_code,
        facebook_code=error.get("code"),
    )
    if "publish_actions" in detail.lower():
        raise BadRequestError(
            "Facebook rejected a user-profile post (publish_actions is deprecated). "
            "This app must post as a Page. Disconnect Facebook, Connect again, "
            "and grant access to a Page you manage."
        )
    if error.get("code") in {10, 200} or "permission" in detail.lower() or "pages_manage_posts" in detail:
        extra = f" Facebook said: {detail[:220]}" if detail else ""
        raise BadRequestError(
            "Facebook did not allow this Page post. Add pages_manage_posts in Meta App "
            "Dashboard (Use cases → Manage everything on your Page → Customize, Ready "
            "for testing), then disconnect and Connect Facebook again."
            + extra
        )
    if detail:
        raise BadRequestError(f"Facebook rejected the test post. {detail[:280]}")
    raise BadRequestError("Facebook rejected the test post. Check the Page token and try again.")
