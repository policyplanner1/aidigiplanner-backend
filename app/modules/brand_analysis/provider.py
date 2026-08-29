"""Turns ExtractedSite (+ an optional user-supplied description) into a
BrandAnalysisResult -- either via one real Gemini call (same
client.interactions.create + JSON response_format pattern as
GeminiTextProvider.generate_concepts) or a deterministic mock, chosen the
same way the creatives pipeline chooses its providers: mock unless
GEMINI_API_KEY is configured (see app.modules.creatives.providers.factory)."""

from __future__ import annotations

import json

import structlog
from google import genai

from app.modules.brand_analysis.extract import ExtractedSite
from app.modules.brand_analysis.schemas import BrandAnalysisResult
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.gemini_common import create_interaction
from app.modules.creatives.providers.gemini_text import strip_markdown_fences

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE = """You are a brand analyst. Read the website content below and extract a \
brand profile as JSON matching this exact schema:
{schema}

Website title: {title}
Meta description: {meta_description}
User-supplied description (if any): {description}
Visible page text (truncated):
{visible_text}

Rules:
- Every field is a best-effort guess from the content above; use "" or [] if you can't tell.
- `tone` and `palette` are short lists of 2-5 items (tone: single words like "professional"; \
palette: colour names or hex codes, not a full design system).
- `category` is the industry/business category (e.g. "Insurance", "E-commerce").
- Output JSON only, no commentary.
"""


def analyze_with_gemini(
    extracted: ExtractedSite, description: str | None, *, settings: CreativeSettings
) -> BrandAnalysisResult:
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _PROMPT_TEMPLATE.format(
        schema=json.dumps(BrandAnalysisResult.model_json_schema()),
        title=extracted.title,
        meta_description=extracted.meta_description,
        description=description or "",
        visible_text=extracted.visible_text,
    )
    interaction = create_interaction(
        client,
        model=settings.models.text_fast,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": BrandAnalysisResult.model_json_schema(),
        },
    )
    raw = strip_markdown_fences(interaction.output_text or "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("brand_analysis_invalid_json", raw=raw[:500])
        return BrandAnalysisResult()
    try:
        return BrandAnalysisResult.model_validate(data)
    except ValueError:
        logger.warning("brand_analysis_failed_validation", raw=raw[:500])
        return BrandAnalysisResult()


def analyze_with_mock(extracted: ExtractedSite, description: str | None) -> BrandAnalysisResult:
    """No network, no cost -- deterministic guesses from whatever the
    regex extractor already found, same role as MockLLMProvider for the
    creatives pipeline."""
    name = extracted.title.split("|")[0].split("-")[0].strip() or "Your Brand"
    return BrandAnalysisResult(
        name=name,
        tagline="",
        description=extracted.meta_description or description or "",
        category="General",
        tone=["professional", "friendly"],
        audience_primary="General consumers",
        audience_secondary="",
        palette=["#0A2540", "#FFFFFF"],
        regulatory_category="",
    )


def get_brand_analysis_result(
    extracted: ExtractedSite,
    description: str | None,
    *,
    settings: CreativeSettings,
    dry_run: bool = False,
) -> BrandAnalysisResult:
    if dry_run or not settings.gemini_api_key:
        return analyze_with_mock(extracted, description)
    return analyze_with_gemini(extracted, description, settings=settings)
