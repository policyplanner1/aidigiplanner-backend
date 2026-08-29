"""Shared helpers for Gemini providers built on the Interactions API
(client.interactions.create) -- ported unchanged from the prototype's
app/providers/gemini_common.py (gemini_text.py today; gemini_image.py in
Sub-phase E will use this too). Veo (Sub-phase F) uses the different
classic long-running-operation API and has no use for this module.

Verified against google-genai==2.19.0 (see gemini_text.py's module
docstring) -- see the prototype's `creative workflow/NOTES.md` for the
original verification trail and why Interaction is imported from its
concrete module path rather than google.genai.interactions (that module's
wildcard re-exports collide two unrelated symbols both named Interaction).

client.interactions.create() raises google.genai._gaos.lib.compat_errors.*
on failure (RateLimitError, InternalServerError, ...), NOT
google.genai.errors.ClientError/ServerError -- those are a different,
unrelated exception hierarchy raised by the classic client.models.*/
client.operations.* API that Veo uses. is_retryable() below checks
compat_errors on purpose.
"""

from __future__ import annotations

import io
import time
from typing import cast

import structlog
from google import genai
from google.genai._gaos.lib import compat_errors
from google.genai._gaos.types.interactions.interaction import Interaction
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)


def is_retryable(exc: BaseException) -> bool:
    """429/5xx only -- never a content-policy rejection (a 4xx other than 429)."""
    if isinstance(exc, compat_errors.APIStatusError):
        status = exc.status_code
        return status is not None and (status == 429 or 500 <= status < 600)
    return False


def _describe_input(input_value: str | list[dict[str, object]]) -> str:
    """Full, human-readable text of what's being sent -- not truncated, so
    the prompt log is genuinely useful for debugging a bad response."""
    if isinstance(input_value, str):
        return input_value
    parts: list[str] = []
    for step in input_value:
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image":
                data = block.get("data")
                if isinstance(data, io.BytesIO):
                    parts.append(f"[reference image, {data.getbuffer().nbytes} bytes]")
                else:
                    parts.append("[reference image]")
    return "\n---\n".join(parts)


@retry(
    retry=retry_if_exception(is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)
def create_interaction(
    client: genai.Client,
    *,
    model: str,
    input: str | list[dict[str, object]],
    response_format: dict[str, object],
    previous_interaction_id: str | None = None,
) -> Interaction:
    logger.info(
        "gemini_request",
        model=model,
        response_format_type=response_format.get("type"),
        has_schema="schema" in response_format,
        previous_interaction_id=previous_interaction_id,
        prompt=_describe_input(input),
    )
    start = time.monotonic()
    try:
        # The SDK's runtime `create()` override normalizes every response to
        # an Interaction; mypy statically sees the pre-override generated
        # overload set instead, whose return type doesn't match at the type
        # level. cast() asserts the documented runtime behavior.
        response = cast(
            Interaction,
            client.interactions.create(
                model=model,
                input=input,
                response_format=response_format,
                previous_interaction_id=previous_interaction_id,
            ),
        )
    except Exception as exc:
        logger.warning(
            "gemini_error",
            model=model,
            elapsed_s=round(time.monotonic() - start, 2),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    image = response.output_image
    logger.info(
        "gemini_response",
        model=model,
        elapsed_s=round(time.monotonic() - start, 2),
        interaction_id=response.id,
        output_text=response.output_text,
        output_image_mime_type=image.mime_type if image else None,
        output_image_bytes=len(image.data) if image and image.data else None,
    )
    return response
