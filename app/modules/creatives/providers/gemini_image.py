"""Real Gemini image provider -- Interactions API. Ported from the
prototype's app/providers/gemini_image.py.

Verified against the installed google-genai SDK: reference images are
passed as content blocks in a structured `input` (a list of one
user_input step whose `content` mixes a text block and one-or-more image
blocks), not a dedicated `reference_images` kwarg. The `data` field of an
outgoing image content block accepts a file-like object directly
(google.genai base64-encodes it for us); the `data` field of a *response*
image is already a base64 str we decode ourselves.
"""

from __future__ import annotations

import base64
import io

from google import genai
from google.genai._gaos.types.interactions.interaction import Interaction

from app.models.enums import CreativeQuality
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import ImageGenerationResult, ImageProvider
from app.modules.creatives.providers.gemini_common import create_interaction

_NO_IMAGE_RETRY_SUFFIX = (
    "\n\nRespond with the generated image only -- do not reply with text, questions, or commentary."
)


def _build_input(
    prompt: str, reference_images: list[bytes] | None
) -> str | list[dict[str, object]]:
    if not reference_images:
        return prompt
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for ref in reference_images:
        content.append({"type": "image", "mime_type": "image/png", "data": io.BytesIO(ref)})
    return [{"type": "user_input", "content": content}]


class GeminiImageProvider(ImageProvider):
    def __init__(self, settings: CreativeSettings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def _create_image_interaction(
        self,
        *,
        model_id: str,
        prompt: str,
        aspect_ratio: str,
        resolution: str,
        reference_images: list[bytes] | None,
        previous_interaction_id: str | None,
    ) -> Interaction:
        return create_interaction(
            self._client,
            model=model_id,
            input=_build_input(prompt, reference_images),
            response_format={
                "type": "image",
                "aspect_ratio": aspect_ratio,
                "image_size": resolution,
            },
            previous_interaction_id=previous_interaction_id,
        )

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str,
        resolution: str,
        quality: CreativeQuality,
        reference_images: list[bytes] | None = None,
        previous_interaction_id: str | None = None,
    ) -> ImageGenerationResult:
        model_id = self._settings.models.image_model_for(quality)
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}\n\nAvoid: {negative_prompt}"

        interaction = self._create_image_interaction(
            model_id=model_id,
            prompt=full_prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            reference_images=reference_images,
            previous_interaction_id=previous_interaction_id,
        )
        output = interaction.output_image

        if output is None or not output.data:
            # The model sometimes responds conversationally instead of
            # generating an image -- a real, non-deterministic quirk (not a
            # content-policy block; the API returns 200 OK with text
            # instead of the requested image format). One bounded retry
            # with a stronger instruction before giving up -- never
            # silently swallowed either way.
            interaction = self._create_image_interaction(
                model_id=model_id,
                prompt=full_prompt + _NO_IMAGE_RETRY_SUFFIX,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                reference_images=reference_images,
                previous_interaction_id=previous_interaction_id,
            )
            output = interaction.output_image

        if output is None or not output.data:
            raise ValueError(
                f"Gemini returned no image for model={model_id!r} after one retry. "
                f"output_text (if any): {interaction.output_text!r}"
            )
        return ImageGenerationResult(
            image_bytes=base64.b64decode(output.data),
            model_id=model_id,
            mime_type=output.mime_type or "image/png",
            interaction_id=interaction.id,
        )
