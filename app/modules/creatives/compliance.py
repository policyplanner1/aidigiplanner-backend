"""The compliance gate: keyword/heuristic checks plus a second, cheap LLM
pass acting as reviewer -- ported unchanged from the prototype's
app/compliance.py.

This is a first-pass safety net, not legal sign-off -- every creative still
needs review by a human compliance owner before publishing (see the
review_status/reviewed_by/reviewed_at fields on CreativeConcept for that
separate human workflow).

Never silently pass a violation through: a concept that still fails after
one retry-with-feedback is rejected and reported (angle, hook, reason), not
shipped quietly.
"""

from __future__ import annotations

import re

from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import Brief, GeneratedConcept
from app.modules.creatives.pricing import CreativeSettings
from app.modules.creatives.prompts import render_prompt
from app.modules.creatives.providers.base import LLMProvider

COMPLIANCE_REVIEW_PROMPT_VERSION = "compliance_review_v1"

_CURRENCY_RE = re.compile(r"(?:₹|\brs\.?|\binr)\s?\d", re.IGNORECASE)
_STARTING_FROM_RE = re.compile(r"starting from", re.IGNORECASE)


def _concept_text_blob(concept: GeneratedConcept) -> str:
    return " ".join(
        [
            concept.hook,
            concept.caption,
            concept.cta,
            concept.on_image_text.headline,
            concept.on_image_text.subhead,
            concept.on_image_text.kicker,
        ]
    )


def check_banned_claims(concept: GeneratedConcept, brand: BrandProfileDTO) -> list[str]:
    blob = _concept_text_blob(concept).lower()
    violations = []
    for claim in brand.compliance.banned_claims:
        if claim.lower().startswith("any "):
            continue  # instructional guidance, not literal -- left to the LLM reviewer
        if claim.lower() in blob:
            violations.append(f"banned claim present: {claim!r}")
    return violations


def check_mandatory_disclaimer(concept: GeneratedConcept, brand: BrandProfileDTO) -> list[str]:
    if brand.compliance.mandatory_disclaimer not in concept.caption:
        return ["mandatory disclaimer missing from caption"]
    return []


def check_partner_names(
    concept: GeneratedConcept, brief: Brief, brand: BrandProfileDTO
) -> list[str]:
    product = brand.product_line(brief.product_line)
    blob = _concept_text_blob(concept).lower()
    notes = brief.extra_notes.lower()
    violations = []
    for partner in product.partners:
        if partner.lower() in blob and partner.lower() not in notes:
            violations.append(f"partner name {partner!r} used but not supplied in the brief")
    return violations


def check_premium_amount_qualifier(concept: GeneratedConcept) -> list[str]:
    blob = _concept_text_blob(concept)
    for match in _CURRENCY_RE.finditer(blob):
        window = blob[max(0, match.start() - 40) : match.end() + 40]
        if not _STARTING_FROM_RE.search(window):
            return ["premium amount mentioned without a 'starting from' qualifier nearby"]
    return []


def run_keyword_checks(
    concept: GeneratedConcept, brief: Brief, brand: BrandProfileDTO
) -> list[str]:
    violations: list[str] = []
    violations += check_banned_claims(concept, brand)
    violations += check_mandatory_disclaimer(concept, brand)
    violations += check_partner_names(concept, brief, brand)
    violations += check_premium_amount_qualifier(concept)
    return violations


def parse_reviewer_verdict(text: str) -> str | None:
    """None means the reviewer passed the concept; otherwise the returned
    string is the violation description. An unparseable response is treated
    as a failure -- never as a silent pass."""
    stripped = text.strip()
    if not stripped:
        return "empty compliance reviewer response"
    first_line = stripped.splitlines()[0].strip()
    if first_line.upper().startswith("PASS"):
        return None
    if first_line.upper().startswith("FAIL"):
        return (
            first_line.split(":", 1)[1].strip()
            if ":" in first_line
            else "flagged by compliance reviewer"
        )
    return f"unparseable compliance reviewer response: {first_line[:200]!r}"


def run_llm_review(
    concept: GeneratedConcept, brand: BrandProfileDTO, llm: LLMProvider, prompts_dir: str
) -> list[str]:
    prompt = render_prompt(
        prompts_dir, COMPLIANCE_REVIEW_PROMPT_VERSION, brand=brand, concept=concept
    )
    result = llm.generate_text(prompt)
    verdict = parse_reviewer_verdict(result.text)
    return [verdict] if verdict else []


def check_concept(
    concept: GeneratedConcept,
    brief: Brief,
    brand: BrandProfileDTO,
    llm: LLMProvider,
    settings: CreativeSettings,
) -> list[str]:
    """Keyword checks run first and are free; the LLM reviewer pass only
    fires when they pass, since a caught keyword violation already tells us
    the concept fails -- no need to also spend on a review call for it."""
    violations = run_keyword_checks(concept, brief, brand)
    if violations:
        return violations
    return run_llm_review(concept, brand, llm, settings.prompts_dir)


def run_compliance_gate(
    concepts: list[GeneratedConcept],
    brief: Brief,
    brand: BrandProfileDTO,
    llm: LLMProvider,
    settings: CreativeSettings,
    *,
    ideate_prompt_version: str,
) -> tuple[list[GeneratedConcept], list[GeneratedConcept]]:
    """Checks every concept. Concepts that fail get exactly one batched
    retry: a single re-ideation call carrying feedback for just the failing
    slots. Concepts still failing after that retry are marked rejected and
    returned separately -- never silently dropped.

    Mutates `concepts` in place (revised candidates replace their original
    index) -- callers that need the final concepts in original index order
    (not just split by accepted/rejected) can keep using the same list
    object after this returns; see pipeline/ideate.py."""
    violations_by_idx = {
        i: check_concept(c, brief, brand, llm, settings) for i, c in enumerate(concepts)
    }
    failing = {i: v for i, v in violations_by_idx.items() if v}

    if failing:
        feedback = {i: "; ".join(v) for i, v in failing.items()}
        revised = llm.generate_concepts(
            brief, brand, len(concepts), ideate_prompt_version, feedback=feedback
        )
        for i in failing:
            if i >= len(revised):
                concepts[i].rejected = True
                concepts[i].rejection_reason = (
                    "; ".join(failing[i]) + " (retry returned too few concepts)"
                )
                continue
            candidate = revised[i]
            candidate_violations = check_concept(candidate, brief, brand, llm, settings)
            if candidate_violations:
                concepts[i].rejected = True
                concepts[i].rejection_reason = "; ".join(candidate_violations)
            else:
                candidate.rejected = False
                candidate.rejection_reason = ""
                concepts[i] = candidate

    accepted = [c for c in concepts if not c.rejected]
    rejected = [c for c in concepts if c.rejected]
    return accepted, rejected
