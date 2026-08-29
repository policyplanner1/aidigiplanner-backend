"""Brand profile DTO consumed by the generation pipeline -- ported from the
standalone prototype's app/brand.py, which loaded this shape from a single
hardcoded brand.yaml. Here it's built from a product's BrandProfile DB row
instead (see brand_profile_from_row below); the pipeline/compliance/prompt
code that consumes it is otherwise unchanged.

Named BrandProfileDTO (not BrandProfile) to avoid confusion with
app.models.brand_profile.BrandProfile, the SQLAlchemy row this is built
from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.brand_profile import BrandProfile as BrandProfileRow


class ProductLineProfile(BaseModel):
    id: str
    label: str
    partners: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)


class VisualIdentity(BaseModel):
    palette: list[str] = Field(default_factory=list)
    heading_font: str = ""
    body_font: str = ""
    style_keywords: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class ComplianceProfile(BaseModel):
    mandatory_disclaimer: str
    secondary_disclaimers: list[str] = Field(default_factory=list)
    banned_claims: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


class Audience(BaseModel):
    primary: str
    secondary: str = ""


class BrandProfileDTO(BaseModel):
    id: str
    name: str
    category: str
    market: str
    audience: Audience
    tone: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    voice: str = ""
    pillars: list[str] = Field(default_factory=list)
    knowledge_notes: list[str] = Field(default_factory=list)
    ai_instructions: str = ""
    product_lines: list[ProductLineProfile] = Field(default_factory=list)
    visual_identity: VisualIdentity
    # Set only when the product has uploaded a logo image -- consumed by
    # worker.py to fetch the bytes for the image/end-card logo composite.
    logo_storage_key: str | None = None
    # Set only when the product has uploaded an avatar image (see
    # app.modules.brand_profiles for the upload endpoint) -- consumed by
    # worker.py to fetch the bytes for ReelStyle.avatar generation.
    avatar_storage_key: str | None = None
    avatar_mime_type: str | None = None
    compliance: ComplianceProfile
    cta_bank: list[str] = Field(default_factory=list)
    hashtag_bank: list[str] = Field(default_factory=list)

    def product_line(self, product_id: str) -> ProductLineProfile:
        for pl in self.product_lines:
            if pl.id == product_id:
                return pl
        raise KeyError(f"unknown product line: {product_id!r}")


def brand_profile_from_row(row: BrandProfileRow) -> BrandProfileDTO:
    return BrandProfileDTO(
        id=row.id,
        name=row.name,
        category=row.category,
        market=row.market,
        audience=Audience(primary=row.audience_primary, secondary=row.audience_secondary),
        tone=row.tone,
        languages=row.languages,
        voice=row.voice,
        pillars=row.pillars,
        knowledge_notes=row.knowledge_notes,
        ai_instructions=row.ai_instructions,
        product_lines=[ProductLineProfile.model_validate(pl) for pl in row.product_lines],
        visual_identity=VisualIdentity.model_validate(row.visual_identity),
        logo_storage_key=row.logo_storage_key,
        avatar_storage_key=row.avatar_storage_key,
        avatar_mime_type=row.avatar_mime_type,
        compliance=ComplianceProfile(
            mandatory_disclaimer=row.compliance_mandatory_disclaimer,
            secondary_disclaimers=row.compliance_secondary_disclaimers,
            banned_claims=row.compliance_banned_claims,
            rules=row.compliance_rules,
        ),
        cta_bank=row.cta_bank,
        hashtag_bank=row.hashtag_bank,
    )
