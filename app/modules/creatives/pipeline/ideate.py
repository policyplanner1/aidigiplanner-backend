"""Brief -> GeneratedConcept, via LLM ideation + the compliance gate --
ported from the prototype's app/pipeline/ideate.py.

Works identically against MockLLMProvider (dry-run / no API key) and
GeminiTextProvider (real calls) -- this function never branches on which
provider it's holding, per the provider-abstraction design in
providers/base.py. Synchronous, like every provider call it makes; the
async arq worker offloads the whole call via asyncio.to_thread(...).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.creatives.brand import Audience, BrandProfileDTO
from app.modules.creatives.compliance import run_compliance_gate
from app.modules.creatives.domain import Brief, GeneratedConcept
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.providers.base import LLMProvider

IDEATE_PROMPT_VERSION = "ideate_v1"


@dataclass(slots=True)
class IdeationResult:
    # Every concept generated, in original index order, each carrying its
    # own final `rejected`/`rejection_reason` -- what worker.py persists as
    # CreativeConcept rows (concept_index = position in this list).
    concepts: list[GeneratedConcept]
    # Convenience views over the same objects in `concepts` above.
    accepted: list[GeneratedConcept]
    rejected: list[GeneratedConcept]
    prompt_version: str


def _apply_brief_overrides(brief: Brief, brand: BrandProfileDTO) -> BrandProfileDTO:
    """Phase 16's "Customize Content" overrides, merged onto the resolved
    brand profile for this one generation request only -- the stored
    BrandProfile row is never touched. tone/audience map onto BrandProfileDTO
    fields directly; objective/offer/festival_occasion/cta_override have no
    dedicated DTO field, so they ride along in extra_notes, the one field
    every prompt template already surfaces as free-text context."""
    updates: dict[str, object] = {}
    if brief.tone_override:
        updates["tone"] = brief.tone_override
    if brief.audience_override:
        updates["audience"] = Audience(
            primary=brief.audience_override, secondary=brand.audience.secondary
        )
    extra_bits = [
        f"Objective: {brief.objective}" if brief.objective else "",
        f"Offer: {brief.offer}" if brief.offer else "",
        f"Festival/occasion: {brief.festival_occasion}" if brief.festival_occasion else "",
        f"CTA: {brief.cta_override}" if brief.cta_override else "",
    ]
    extra_text = "\n".join(bit for bit in extra_bits if bit)
    if extra_text:
        brief.extra_notes = f"{brief.extra_notes}\n{extra_text}".strip()
    return brand.model_copy(update=updates) if updates else brand


def run_ideation(
    brief: Brief, brand: BrandProfileDTO, llm: LLMProvider, settings: CreativeSettings
) -> IdeationResult:
    brand = _apply_brief_overrides(brief, brand)
    concepts = llm.generate_concepts(brief, brand, brief.concept_count, IDEATE_PROMPT_VERSION)
    for concept in concepts:
        # Deterministic, not left to the model or the gate: the on-image
        # disclaimer is always exactly the brand string.
        concept.disclaimer_line = brand.compliance.mandatory_disclaimer

    # run_compliance_gate mutates `concepts` in place (revised candidates
    # replace their original index), so it's still the authoritative
    # original-order list after this call -- see its docstring.
    accepted, rejected = run_compliance_gate(
        concepts, brief, brand, llm, settings, ideate_prompt_version=IDEATE_PROMPT_VERSION
    )
    return IdeationResult(
        concepts=concepts,
        accepted=accepted,
        rejected=rejected,
        prompt_version=IDEATE_PROMPT_VERSION,
    )
