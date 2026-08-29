from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.brand_profile import BrandProfile
from app.models.enums import BrandAnalysisScope, CompanyOnboardingStep
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.brand_profiles.resolution import get_own_brand_profile
from app.modules.brand_profiles.schemas import UpsertBrandProfileRequest
from app.modules.companies.onboarding import advance_onboarding_step
from app.modules.storage.base import StorageService

ALLOWED_IMAGE_MIME_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

# Each image slot is a (storage_key attribute, mime_type attribute, key
# prefix under the owner's "brand/" namespace) triple. One helper method
# drives all four instead of four near-identical copies.
_IMAGE_SLOTS = {
    "avatar": ("avatar_storage_key", "avatar_mime_type", "avatar"),
    "logo": ("logo_storage_key", "logo_mime_type", "logo"),
    "logo-dark": ("dark_logo_storage_key", "dark_logo_mime_type", "logo-dark"),
    "icon": ("icon_storage_key", "icon_mime_type", "icon"),
}


class BrandProfileService:
    def __init__(self, session: AsyncSession, audit: AuditService, storage: StorageService) -> None:
        self._session = session
        self._audit = audit
        self._storage = storage

    async def get_brand_profile(self, scope: BrandAnalysisScope, owner_id: str) -> BrandProfile:
        profile = await get_own_brand_profile(self._session, scope, owner_id)
        if profile is None:
            raise NotFoundError("This has no brand profile yet.")
        return profile

    async def upsert_brand_profile(
        self,
        scope: BrandAnalysisScope,
        owner_id: str,
        actor: User,
        payload: UpsertBrandProfileRequest,
    ) -> BrandProfile:
        profile = await get_own_brand_profile(self._session, scope, owner_id)
        created = profile is None
        if profile is None:
            profile = BrandProfile(scope=scope, owner_id=owner_id, created_by=actor.id)
            self._session.add(profile)

        profile.name = payload.name
        profile.tagline = payload.tagline
        profile.description = payload.description
        profile.contact_email = payload.contact_email
        profile.contact_number = payload.contact_number
        profile.social_links = payload.social_links
        profile.regulatory_category = payload.regulatory_category
        profile.category = payload.category
        profile.market = payload.market
        profile.audience_primary = payload.audience_primary
        profile.audience_secondary = payload.audience_secondary
        profile.tone = payload.tone
        profile.languages = payload.languages
        profile.voice = payload.voice
        profile.pillars = payload.pillars
        profile.website_url = payload.website_url
        profile.domains = payload.domains
        profile.knowledge_notes = payload.knowledge_notes
        profile.knowledge_urls = payload.knowledge_urls
        profile.ai_instructions = payload.ai_instructions
        profile.visual_identity = payload.visual_identity.model_dump()
        profile.compliance_mandatory_disclaimer = payload.compliance_mandatory_disclaimer
        profile.compliance_secondary_disclaimers = payload.compliance_secondary_disclaimers
        profile.compliance_banned_claims = payload.compliance_banned_claims
        profile.compliance_rules = payload.compliance_rules
        profile.cta_bank = payload.cta_bank
        profile.hashtag_bank = payload.hashtag_bank
        profile.product_lines = [pl.model_dump() for pl in payload.product_lines]

        if scope == BrandAnalysisScope.company:
            await advance_onboarding_step(
                self._session, owner_id, CompanyOnboardingStep.brand_profile_completed
            )

        await self._audit.log(
            action="brand_profile.created" if created else "brand_profile.updated",
            actor_user_id=actor.id,
            resource_type="brand_profile",
            metadata={"scope": scope.value, "owner_id": owner_id},
        )
        await self._session.commit()
        return profile

    async def upload_image(
        self,
        scope: BrandAnalysisScope,
        owner_id: str,
        actor: User,
        *,
        slot: str,
        data: bytes,
        content_type: str,
    ) -> BrandProfile:
        profile = await self.get_brand_profile(scope, owner_id)
        ext = ALLOWED_IMAGE_MIME_TYPES.get(content_type)
        if ext is None:
            raise BadRequestError(
                f"Unsupported image type {content_type!r}. "
                f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_MIME_TYPES))}."
            )
        storage_key_attr, mime_type_attr, key_prefix = _IMAGE_SLOTS[slot]

        # Overwrites in place at a fixed key (not per-upload-unique) --
        # there's only ever one current image per slot per owner, and a
        # stale key left behind after a re-upload would just be a permanent
        # orphan in storage with nothing ever pointing at it again.
        key = f"{scope.value}/{owner_id}/brand/{key_prefix}.{ext}"
        await self._storage.save(key=key, data=data, content_type=content_type)
        setattr(profile, storage_key_attr, key)
        setattr(profile, mime_type_attr, content_type)

        await self._audit.log(
            action=f"brand_profile.{slot.replace('-', '_')}_uploaded",
            actor_user_id=actor.id,
            resource_type="brand_profile",
            metadata={"scope": scope.value, "owner_id": owner_id},
        )
        await self._session.commit()
        return profile

    async def get_image_bytes(
        self, scope: BrandAnalysisScope, owner_id: str, *, slot: str
    ) -> tuple[bytes, str]:
        profile = await self.get_brand_profile(scope, owner_id)
        storage_key_attr, mime_type_attr, _ = _IMAGE_SLOTS[slot]
        storage_key = getattr(profile, storage_key_attr)
        if storage_key is None:
            raise NotFoundError(f"This has no {slot} image uploaded yet.")
        data = await self._storage.read(storage_key)
        return data, getattr(profile, mime_type_attr) or "application/octet-stream"
