"""Fetches a candidate brand website for app.modules.brand_analysis.extract
to read. Deliberately minimal -- a plain GET with a byte cap and a short
timeout, not a crawler. No JS rendering: whatever's in the initial HTML
response is all AI brand analysis ever sees (Phase 5's "Reading website" is
one request, not a browser)."""

from __future__ import annotations

import httpx

from app.core.exceptions import BadRequestError

_TIMEOUT_S = 10.0
_MAX_BYTES = 2_000_000
_USER_AGENT = "aiDigiPlannerBrandAnalysis/1.0 (+https://aidigiplanner.example.com)"


async def fetch_website(url: str) -> str:
    """Returns the response body as text. Raises BadRequestError on any
    network failure, non-2xx status, or a non-HTML content type -- all
    treated the same way by the caller (Phase 5 falls back to whatever
    `description` text was also supplied, if any)."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT_S, headers=headers
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise BadRequestError(
                        f"Could not fetch {url!r}: HTTP {response.status_code}."
                    )
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type and content_type != "":
                    raise BadRequestError(f"{url!r} did not return an HTML page.")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_BYTES:
                        break
                body = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise BadRequestError(f"Could not fetch {url!r}: {exc}") from exc

    return body.decode("utf-8", errors="replace")
