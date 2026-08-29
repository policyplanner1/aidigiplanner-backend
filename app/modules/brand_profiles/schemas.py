from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import BrandAnalysisScope


class VisualIdentityInput(BaseModel):
    palette: list[str] = Field(default_factory=list)
    heading_font: str = ""
    body_font: str = ""
    style_keywords: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class ProductLineInput(BaseModel):
    id: str
    label: str
    partners: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)


class BrandProfilePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: BrandAnalysisScope
    owner_id: str
    name: str
    tagline: str
    description: str
    contact_email: str | None
    contact_number: str | None
    social_links: dict[str, str]
    regulatory_category: str
    category: str
    market: str
    audience_primary: str
    audience_secondary: str
    tone: list[str]
    languages: list[str]
    voice: str
    pillars: list[str]
    website_url: str | None
    domains: list[str]
    knowledge_notes: list[str]
    knowledge_urls: list[str]
    ai_instructions: str
    visual_identity: dict[str, Any]
    # Non-null means that image slot has been uploaded (see the dedicated
    # upload/download endpoints) -- the raw storage key is never exposed.
    logo_mime_type: str | None
    dark_logo_mime_type: str | None
    icon_mime_type: str | None
    avatar_mime_type: str | None
    compliance_mandatory_disclaimer: str
    compliance_secondary_disclaimers: list[str]
    compliance_banned_claims: list[str]
    compliance_rules: list[str]
    cta_bank: list[str]
    hashtag_bank: list[str]
    product_lines: list[dict[str, Any]]
    created_by: str
    created_at: UTCDatetime
    updated_at: UTCDatetime


class UpsertBrandProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tagline: str = ""
    description: str = ""
    contact_email: str | None = None
    contact_number: str | None = None
    social_links: dict[str, str] = Field(default_factory=dict)
    regulatory_category: str = ""
    category: str = Field(min_length=1, max_length=255)
    market: str = Field(min_length=1, max_length=255)
    audience_primary: str
    audience_secondary: str = ""
    tone: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    voice: str = ""
    pillars: list[str] = Field(default_factory=list)
    website_url: str | None = None
    domains: list[str] = Field(default_factory=list)
    knowledge_notes: list[str] = Field(default_factory=list)
    knowledge_urls: list[str] = Field(default_factory=list)
    ai_instructions: str = ""
    visual_identity: VisualIdentityInput = Field(default_factory=VisualIdentityInput)
    compliance_mandatory_disclaimer: str = ""
    compliance_secondary_disclaimers: list[str] = Field(default_factory=list)
    compliance_banned_claims: list[str] = Field(default_factory=list)
    compliance_rules: list[str] = Field(default_factory=list)
    cta_bank: list[str] = Field(default_factory=list)
    hashtag_bank: list[str] = Field(default_factory=list)
    product_lines: list[ProductLineInput] = Field(default_factory=list)
