from typing import Any

from sqlalchemy import CHAR, JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import BrandAnalysisScope


class BrandProfile(UUIDPKMixin, TimestampMixin, Base):
    """One per (scope, owner) -- the brand identity/voice/compliance
    document every creative-generation request reads from, resolved via
    app.modules.brand_profiles.resolution.resolve_effective_brand_profile
    (which 400s if nothing resolves for a product/sub-product).

    `owner_id` intentionally has no FK -- it points at companies.id,
    products.id, or sub_products.id depending on `scope`, and no single
    column can carry three different foreign keys."""

    __tablename__ = "brand_profiles"
    __table_args__ = (
        UniqueConstraint("scope", "owner_id", name="uq_brand_profiles_scope_owner_id"),
    )

    scope: Mapped[BrandAnalysisScope] = mapped_column(
        enum_column_type(BrandAnalysisScope), nullable=False, index=True
    )
    owner_id: Mapped[str] = mapped_column(CHAR(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Phase 6's "Additional Brand Details" (collapsed-by-default) fields --
    # populated by AI brand analysis (see app.modules.brand_analysis) or
    # filled in manually via the upsert endpoint, all optional.
    tagline: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # {platform: url}, keyed by SocialPlatform values -- discovered by brand
    # analysis or entered manually, distinct from SocialAccount (which is
    # this product's own connected/managed accounts, not just a link found
    # on the brand's website).
    social_links: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    regulatory_category: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    market: Mapped[str] = mapped_column(String(255), nullable=False)
    audience_primary: Mapped[str] = mapped_column(Text, nullable=False)
    audience_secondary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tone: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Long-form voice/tone description, injected into the ideation prompt
    # verbatim (see prompts/ideate_v1.j2's "BRAND VOICE (verbatim)" block).
    voice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pillars: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    domains: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Free-text reference notes, injected into the ideation prompt as
    # context. knowledge_urls is stored only -- no fetching/scraping
    # capability exists yet, so it's never injected into a prompt.
    knowledge_notes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    knowledge_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ai_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # {palette: [str], heading_font: str, body_font: str, style_keywords: [str], avoid: [str]}
    visual_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Three independent upload slots, same convention as avatar_storage_key
    # below -- opaque StorageService keys, never raw filesystem paths.
    logo_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dark_logo_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dark_logo_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    icon_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Uploaded once, reused by every avatar-style reel this product
    # generates (see ReelStyle.avatar). Key handed to StorageService, never
    # a raw filesystem path -- same convention as CreativeAsset.storage_key.
    avatar_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    compliance_mandatory_disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    compliance_secondary_disclaimers: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    compliance_banned_claims: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    compliance_rules: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cta_bank: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    hashtag_bank: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # [{id, label, partners: [str], hooks: [str]}, ...]
    product_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
