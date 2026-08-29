"""Pure-unit tests for the compliance gate -- ported from the prototype's
tests/test_compliance.py. No DB, no HTTP client; the `brand` fixture (a
realistic compliance profile) lives in conftest.py."""

from typing import cast

import pytest

from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.compliance import (
    check_banned_claims,
    check_mandatory_disclaimer,
    check_partner_names,
    check_premium_amount_qualifier,
    parse_reviewer_verdict,
    run_compliance_gate,
    run_llm_review,
)
from app.modules.creatives.domain import Brief
from app.modules.creatives.pricing import CreativeSettings, get_creative_settings
from tests.creatives_fixtures import FakeLLMProvider, make_concept


@pytest.fixture
def settings() -> CreativeSettings:
    return get_creative_settings()


@pytest.fixture
def travel_brief() -> Brief:
    return Brief(product_line="travel", topic="visa cover", format="post")


class TestBannedClaims:
    LITERAL_BANNED_CLAIMS = [
        "guaranteed claim approval",
        "100% claim settlement",
        "cheapest in India",
        "no documents required",
        "instant claim payout",
        "free insurance",
    ]

    @pytest.mark.parametrize("claim", LITERAL_BANNED_CLAIMS)
    def test_literal_claim_in_caption_is_flagged(self, brand: BrandProfileDTO, claim: str) -> None:
        concept = make_concept(caption=f"Some intro. {claim.capitalize()}! More text.")
        violations = check_banned_claims(concept, brand)
        assert any(claim in v for v in violations)

    @pytest.mark.parametrize("claim", LITERAL_BANNED_CLAIMS)
    def test_claim_is_case_insensitive(self, brand: BrandProfileDTO, claim: str) -> None:
        concept = make_concept(caption=f"INTRO {claim.upper()} more")
        violations = check_banned_claims(concept, brand)
        assert violations

    @pytest.mark.parametrize("claim", LITERAL_BANNED_CLAIMS)
    def test_claim_can_appear_in_hook_or_cta(self, brand: BrandProfileDTO, claim: str) -> None:
        concept = make_concept(hook=claim, cta="Get a quote in 60 seconds — link in bio")
        violations = check_banned_claims(concept, brand)
        assert violations

    def test_clean_concept_has_no_banned_claim_violations(self, brand: BrandProfileDTO) -> None:
        concept = make_concept()
        assert check_banned_claims(concept, brand) == []

    def test_instructional_entry_is_not_literally_matched(self, brand: BrandProfileDTO) -> None:
        # "any absolute or superlative claim..." is guidance for the LLM
        # reviewer, not a literal phrase -- it must never match by substring.
        concept = make_concept(caption="This text says nothing absolute or superlative at all.")
        violations = check_banned_claims(concept, brand)
        assert violations == []


class TestMandatoryDisclaimer:
    def test_present_passes(self, brand: BrandProfileDTO) -> None:
        concept = make_concept(caption=f"Body text.\n\n{brand.compliance.mandatory_disclaimer}")
        assert check_mandatory_disclaimer(concept, brand) == []

    def test_missing_fails(self, brand: BrandProfileDTO) -> None:
        concept = make_concept(caption="Body text with no disclaimer at all.")
        violations = check_mandatory_disclaimer(concept, brand)
        assert len(violations) == 1
        assert "disclaimer" in violations[0]


class TestPartnerNames:
    def test_partner_named_without_brief_mention_is_flagged(
        self, brand: BrandProfileDTO, travel_brief: Brief
    ) -> None:
        concept = make_concept(caption="Backed by Bajaj Allianz for total peace of mind.")
        violations = check_partner_names(concept, travel_brief, brand)
        assert any("Bajaj Allianz" in v for v in violations)

    def test_partner_named_with_brief_mention_passes(self, brand: BrandProfileDTO) -> None:
        brief = Brief(
            product_line="travel",
            topic="visa cover",
            format="post",
            extra_notes="Feature our partner Bajaj Allianz explicitly this time.",
        )
        concept = make_concept(caption="Backed by Bajaj Allianz for total peace of mind.")
        assert check_partner_names(concept, brief, brand) == []

    def test_no_partner_mentioned_passes(self, brand: BrandProfileDTO, travel_brief: Brief) -> None:
        concept = make_concept()
        assert check_partner_names(concept, travel_brief, brand) == []

    def test_motor_has_no_partners_configured(self, brand: BrandProfileDTO) -> None:
        brief = Brief(product_line="motor", topic="renewal", format="post")
        concept = make_concept(caption="Some renewal copy mentioning nothing special.")
        assert check_partner_names(concept, brief, brand) == []


class TestPremiumAmountQualifier:
    def test_currency_without_starting_from_is_flagged(self) -> None:
        concept = make_concept(caption="Cover starts at just ₹499 for your trip.")
        violations = check_premium_amount_qualifier(concept)
        assert violations

    def test_currency_with_starting_from_nearby_passes(self) -> None:
        concept = make_concept(caption="Plans starting from ₹499 for your trip. T&C apply.")
        assert check_premium_amount_qualifier(concept) == []

    def test_no_currency_mentioned_passes(self) -> None:
        concept = make_concept(caption="No numbers here at all, just plain reassurance.")
        assert check_premium_amount_qualifier(concept) == []

    def test_rs_prefix_also_detected(self) -> None:
        concept = make_concept(caption="Just Rs 499 a month, no strings attached.")
        assert check_premium_amount_qualifier(concept)

    def test_word_ending_in_rs_followed_by_a_number_is_not_a_false_positive(self) -> None:
        # Regression: "offers 100%" contains "...rs 1..." as a raw substring
        # of "offe|rs| 100%" -- the check must require a word boundary
        # before "rs", not just look for the bare letters.
        concept = make_concept(caption="This offers 100% peace of mind on every trip.")
        assert check_premium_amount_qualifier(concept) == []


class TestParseReviewerVerdict:
    def test_pass_returns_none(self) -> None:
        assert parse_reviewer_verdict("PASS") is None

    def test_pass_case_insensitive(self) -> None:
        assert parse_reviewer_verdict("pass") is None

    def test_fail_with_colon_extracts_reason(self) -> None:
        assert (
            parse_reviewer_verdict("FAIL: implies certain claim payout")
            == "implies certain claim payout"
        )

    def test_fail_without_colon_uses_generic_reason(self) -> None:
        assert parse_reviewer_verdict("FAIL") == "flagged by compliance reviewer"

    def test_empty_response_is_a_failure_not_a_silent_pass(self) -> None:
        assert parse_reviewer_verdict("") is not None

    def test_garbage_response_is_a_failure_not_a_silent_pass(self) -> None:
        result = parse_reviewer_verdict("I'm not sure, maybe it's fine?")
        assert result is not None
        assert "unparseable" in result


class TestRunLlmReview:
    def test_pass_verdict_yields_no_violations(
        self, brand: BrandProfileDTO, settings: CreativeSettings
    ) -> None:
        llm = FakeLLMProvider(concepts_by_call=[], review_verdicts=["PASS"])
        violations = run_llm_review(make_concept(), brand, llm, settings.prompts_dir)
        assert violations == []

    def test_fail_verdict_yields_one_violation(
        self, brand: BrandProfileDTO, settings: CreativeSettings
    ) -> None:
        llm = FakeLLMProvider(
            concepts_by_call=[], review_verdicts=["FAIL: superlative pricing claim"]
        )
        violations = run_llm_review(make_concept(), brand, llm, settings.prompts_dir)
        assert violations == ["superlative pricing claim"]


class TestRunComplianceGate:
    def test_all_clean_concepts_pass_with_no_retry(
        self, brand: BrandProfileDTO, travel_brief: Brief, settings: CreativeSettings
    ) -> None:
        concepts = [make_concept(), make_concept(hook="Second hook")]
        llm = FakeLLMProvider(concepts_by_call=[], review_verdicts=["PASS", "PASS"])
        accepted, rejected = run_compliance_gate(
            concepts, travel_brief, brand, llm, settings, ideate_prompt_version="ideate_v1"
        )
        assert len(accepted) == 2
        assert rejected == []
        assert llm.generate_concepts_calls == []  # no retry needed

    def test_violation_triggers_one_retry_and_recovers(
        self, brand: BrandProfileDTO, travel_brief: Brief, settings: CreativeSettings
    ) -> None:
        disclaimer = brand.compliance.mandatory_disclaimer
        bad = make_concept(caption=f"This offers 100% claim settlement guaranteed.\n\n{disclaimer}")
        good_replacement = make_concept(hook="Revised hook", caption=f"Clean copy.\n\n{disclaimer}")
        llm = FakeLLMProvider(
            concepts_by_call=[[good_replacement]],
            review_verdicts=["PASS"],  # only the replacement goes through LLM review
        )
        accepted, rejected = run_compliance_gate(
            [bad], travel_brief, brand, llm, settings, ideate_prompt_version="ideate_v1"
        )
        assert len(accepted) == 1
        assert accepted[0].hook == "Revised hook"
        assert rejected == []
        assert len(llm.generate_concepts_calls) == 1
        assert llm.generate_concepts_calls[0]["feedback"] == {
            0: "banned claim present: '100% claim settlement'"
        }

    def test_violation_still_failing_after_retry_is_rejected_with_reason(
        self, brand: BrandProfileDTO, travel_brief: Brief, settings: CreativeSettings
    ) -> None:
        disclaimer = brand.compliance.mandatory_disclaimer
        bad = make_concept(caption=f"This offers 100% claim settlement guaranteed.\n\n{disclaimer}")
        still_bad = make_concept(
            caption=f"Still cheapest in India, guaranteed claim approval.\n\n{disclaimer}"
        )
        llm = FakeLLMProvider(concepts_by_call=[[still_bad]], review_verdicts=["PASS"])
        accepted, rejected = run_compliance_gate(
            [bad], travel_brief, brand, llm, settings, ideate_prompt_version="ideate_v1"
        )
        assert accepted == []
        assert len(rejected) == 1
        assert rejected[0].rejected is True
        assert "claim" in rejected[0].rejection_reason.lower()

    def test_only_failing_concepts_are_included_in_retry_feedback(
        self, brand: BrandProfileDTO, travel_brief: Brief, settings: CreativeSettings
    ) -> None:
        disclaimer = brand.compliance.mandatory_disclaimer
        good = make_concept(hook="Good hook", caption=f"Clean copy.\n\n{disclaimer}")
        bad = make_concept(
            hook="Bad hook", caption=f"Free insurance for everyone, always.\n\n{disclaimer}"
        )
        replacement = make_concept(hook="Fixed hook", caption=f"Clean fixed copy.\n\n{disclaimer}")
        llm = FakeLLMProvider(
            concepts_by_call=[[good, replacement]], review_verdicts=["PASS", "PASS"]
        )
        accepted, rejected = run_compliance_gate(
            [good, bad], travel_brief, brand, llm, settings, ideate_prompt_version="ideate_v1"
        )
        assert len(llm.generate_concepts_calls) == 1
        feedback = cast("dict[int, str]", llm.generate_concepts_calls[0]["feedback"])
        assert list(feedback.keys()) == [1]
        assert len(accepted) == 2
        assert rejected == []

    def test_rejected_concepts_never_silently_dropped(
        self, brand: BrandProfileDTO, travel_brief: Brief, settings: CreativeSettings
    ) -> None:
        """Total concepts in must equal accepted + rejected out -- nothing vanishes."""
        disclaimer = brand.compliance.mandatory_disclaimer
        bad = make_concept(
            caption=f"Free insurance, guaranteed claim approval, cheapest in India.\n\n{disclaimer}"
        )
        still_bad = make_concept(
            caption=f"Still free insurance for all, guaranteed.\n\n{disclaimer}"
        )
        llm = FakeLLMProvider(concepts_by_call=[[still_bad]], review_verdicts=["PASS"])
        accepted, rejected = run_compliance_gate(
            [bad], travel_brief, brand, llm, settings, ideate_prompt_version="ideate_v1"
        )
        assert len(accepted) + len(rejected) == 1
