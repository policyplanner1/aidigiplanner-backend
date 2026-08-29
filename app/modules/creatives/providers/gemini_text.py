"""Real Gemini text provider -- Interactions API (client.interactions.create)
-- ported from the prototype's app/providers/gemini_text.py.

Two call shapes live here:
- generate_concepts: structured JSON output constrained by GeneratedConcept's
  schema, via response_format={"type": "text", "mime_type": "application/json",
  "schema": ...}.
- generate_text: freeform completion, used by the compliance reviewer pass.

Retry policy and Interaction creation are shared with gemini_image.py
(Sub-phase E) via gemini_common.py. A content-policy rejection or a
malformed-JSON response is never retried -- it surfaces immediately so the
caller (the compliance gate, or the request) sees it plainly.
"""

from __future__ import annotations

import json
import re

import structlog
from google import genai
from pydantic import BaseModel

from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import Angle, Brief, GeneratedConcept
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.prompts import render_prompt
from app.modules.creatives.providers.base import LLMProvider, TextGenerationResult
from app.modules.creatives.providers.gemini_common import create_interaction

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def strip_markdown_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


class _ConceptBatch(BaseModel):
    """Only used to derive the JSON schema sent as response_format -- each
    concept is validated individually on the way back in (see
    generate_concepts), since the schema can constrain *shape* (types,
    max_length) but not the word-count business rules GeneratedConcept's own
    validators enforce, and one bad concept shouldn't sink the whole batch."""

    concepts: list[GeneratedConcept]


class GeminiTextProvider(LLMProvider):
    def __init__(self, settings: CreativeSettings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate_concepts(
        self,
        brief: Brief,
        brand: BrandProfileDTO,
        count: int,
        prompt_version: str,
        *,
        feedback: dict[int, str] | None = None,
    ) -> list[GeneratedConcept]:
        product = brand.product_line(brief.product_line)
        prompt = render_prompt(
            self._settings.prompts_dir,
            prompt_version,
            brand=brand,
            product=product,
            brief=brief,
            count=count,
            feedback=feedback or {},
            angle_options=[a.value for a in Angle],
        )
        model_id = self._settings.models.text_model_for(brief.quality)
        interaction = create_interaction(
            self._client,
            model=model_id,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _ConceptBatch.model_json_schema(),
            },
        )
        raw = strip_markdown_fences(interaction.output_text or "")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Gemini returned invalid JSON for prompt_version={prompt_version!r}: {exc}\n"
                f"Raw output (truncated): {raw[:500]!r}"
            ) from exc

        raw_concepts = data.get("concepts") if isinstance(data, dict) else None
        if not isinstance(raw_concepts, list) or not raw_concepts:
            raise ValueError(
                f"Gemini's response for prompt_version={prompt_version!r} has no concepts. "
                f"Raw output (truncated): {raw[:500]!r}"
            )

        concepts: list[GeneratedConcept] = []
        for i, item in enumerate(raw_concepts):
            try:
                concept = GeneratedConcept.model_validate(item)
            except ValueError as exc:
                # Valid JSON that fails a business-rule validator (e.g. a
                # headline over 6 words) is real and happens in practice --
                # drop just that concept and keep the rest of the batch
                # instead of failing the whole call.
                logger.warning(
                    "concept_failed_validation",
                    prompt_version=prompt_version,
                    concept_index=i,
                    error=str(exc),
                )
                continue
            # Deterministic, not left to the model: the short on-image
            # disclaimer must be exactly the brand string, always.
            concept.disclaimer_line = brand.compliance.mandatory_disclaimer
            concept.rejected = False
            concept.rejection_reason = ""
            concepts.append(concept)

        if not concepts:
            raise ValueError(
                f"None of Gemini's {len(raw_concepts)} concepts for "
                f"prompt_version={prompt_version!r} passed validation. "
                f"Raw output (truncated): {raw[:500]!r}"
            )
        return concepts

    def generate_text(self, prompt: str, *, system: str = "") -> TextGenerationResult:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        model_id = self._settings.models.text_fast
        interaction = create_interaction(
            self._client, model=model_id, input=full_prompt, response_format={"type": "text"}
        )
        return TextGenerationResult(text=interaction.output_text or "", model_id=model_id)
