from pydantic import BaseModel, Field, model_validator


class AnalyzeBrandRequest(BaseModel):
    """Phase 5's "Help AI understand your ..." step: either a website to
    read, or a short description when there's no website yet -- at least
    one is required."""

    website_url: str | None = Field(default=None, max_length=500)
    description: str | None = None
    # Forces the mock analyzer even when GEMINI_API_KEY is configured --
    # same escape hatch as GenerateCreativesRequest.dry_run in the creatives
    # module (app.modules.creatives.schemas), and for the same reason: a
    # caller can exercise this endpoint at zero cost/network on demand.
    dry_run: bool = False

    @model_validator(mode="after")
    def _require_one_input(self) -> "AnalyzeBrandRequest":
        if not self.website_url and not self.description:
            raise ValueError("Provide either website_url or description.")
        return self


class BrandAnalysisResult(BaseModel):
    """What the Gemini call (or the mock provider) produces -- fed into
    BrandAnalysisService.analyze() to upsert a BrandProfile row. Field
    names deliberately mirror BrandProfile's own columns so the mapping in
    the service is a straight assignment."""

    name: str = ""
    tagline: str = ""
    description: str = ""
    category: str = ""
    tone: list[str] = Field(default_factory=list)
    audience_primary: str = ""
    audience_secondary: str = ""
    palette: list[str] = Field(default_factory=list)
    regulatory_category: str = ""
