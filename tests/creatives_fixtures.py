"""Shared test doubles/factories for the creatives pipeline's pure-unit
tests -- ported from the prototype's tests/conftest.py (FakeLLMProvider,
make_concept)."""

from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import Angle, Brief, GeneratedConcept, OnImageText
from app.modules.creatives.providers.base import LLMProvider, TextGenerationResult


class FakeLLMProvider(LLMProvider):
    """Test double with fully scripted responses -- no network, no
    dependency on MockLLMProvider's own (unrelated) generation logic.

    - `concepts_by_call` is a queue: each generate_concepts() call pops the
      next batch. Lets a test script "first attempt has a violation, retry
      attempt is clean" scenarios precisely.
    - `review_verdicts` is a queue of raw reviewer response strings consumed
      one per generate_text() call (in the PASS/FAIL protocol).
    """

    def __init__(
        self,
        concepts_by_call: list[list[GeneratedConcept]],
        review_verdicts: list[str] | None = None,
    ) -> None:
        self._concepts_by_call = list(concepts_by_call)
        self._review_verdicts = list(review_verdicts or [])
        self.generate_concepts_calls: list[dict[str, object]] = []
        self.generate_text_calls: list[str] = []

    def generate_concepts(
        self,
        brief: Brief,
        brand: BrandProfileDTO,
        count: int,
        prompt_version: str,
        *,
        feedback: dict[int, str] | None = None,
    ) -> list[GeneratedConcept]:
        self.generate_concepts_calls.append({"count": count, "feedback": feedback})
        if not self._concepts_by_call:
            raise AssertionError(
                "FakeLLMProvider.generate_concepts called more times than scripted"
            )
        return self._concepts_by_call.pop(0)

    def generate_text(self, prompt: str, *, system: str = "") -> TextGenerationResult:
        self.generate_text_calls.append(prompt)
        if not self._review_verdicts:
            raise AssertionError("FakeLLMProvider.generate_text called more times than scripted")
        return TextGenerationResult(text=self._review_verdicts.pop(0), model_id="fake-text")


def make_concept(**overrides: object) -> GeneratedConcept:
    defaults: dict[str, object] = dict(
        angle=Angle.scenario,
        hook="Your flight is cancelled in Frankfurt.",
        caption="What now?\n\nInsurance is the subject matter of solicitation.",
        hashtags=[f"#Tag{i}" for i in range(8)],
        cta="Get a quote in 60 seconds — link in bio",
        on_image_text=OnImageText(headline="Then what?", subhead="Cover that travels with you"),
        image_prompt="A traveller at an airport help desk, editorial style",
        disclaimer_line="Insurance is the subject matter of solicitation.",
    )
    defaults.update(overrides)
    return GeneratedConcept(**defaults)
