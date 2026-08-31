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
Visible page text (truncated, extracted without running JavaScript -- may be sparse or empty \
for a JS-rendered site):
{visible_text}

Rules:
- Commit to your single best guess for every field from whatever you can read (name, title, \
tagline, nav/footer links, industry jargon, product names, etc.) -- do not leave a field "" or \
[] just because you are not 100% certain. Only leave a field empty if the page gave you \
literally nothing to infer it from (e.g. the fetch itself failed).
- `category` is the industry/business category (e.g. "Insurance", "E-commerce") -- infer it from \
context clues (product names, jargon, claims) even if the page never states it outright.
- `audience_primary` is a short phrase, not a placeholder like "General consumers" -- infer who \
the site is actually selling/talking to from its language and offering.
- `tone` and `palette` are short lists of 2-5 items (tone: single words like "professional"). \
For `palette`, use actual colour names/hex codes you can see on the page if visible; if you \
cannot see the page's visual styling, infer 2-3 plausible brand colours from the site's industry \
and stated tone instead of leaving the list empty.
- Output JSON only, no commentary.
"""

_URL_INSTRUCTION = """
The website to analyze is: {website_url}
Use the url_context tool to fetch and read this page directly -- the "visible page text" above \
was extracted with a plain HTTP request and did not execute JavaScript, so it can be sparse or \
empty for a JavaScript-rendered (SPA) site. Prefer what you find by visiting the URL yourself \
over the extracted text whenever they disagree or the extracted text looks incomplete. Actually \
call the tool before answering -- do not skip it just because the text above already has \
something in it.
"""


def analyze_with_gemini(
    extracted: ExtractedSite,
    description: str | None,
    *,
    settings: CreativeSettings,
    website_url: str | None = None,
) -> BrandAnalysisResult:
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _PROMPT_TEMPLATE.format(
        schema=json.dumps(BrandAnalysisResult.model_json_schema()),
        title=extracted.title,
        meta_description=extracted.meta_description,
        description=description or "",
        visible_text=extracted.visible_text,
    )
    tools: list[dict[str, object]] | None = None
    if website_url:
        prompt += _URL_INSTRUCTION.format(website_url=website_url)
        tools = [{"type": "url_context"}]
    interaction = create_interaction(
        client,
        model=settings.models.text_fast,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": BrandAnalysisResult.model_json_schema(),
        },
        tools=tools,
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
    website_url: str | None = None,
) -> BrandAnalysisResult:
    if dry_run or not settings.gemini_api_key:
        return analyze_with_mock(extracted, description)
    return analyze_with_gemini(extracted, description, settings=settings, website_url=website_url)
