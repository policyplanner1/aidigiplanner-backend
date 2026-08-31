import asyncio

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand_profile import BrandProfile
from app.models.enums import BrandAnalysisScope, CompanyOnboardingStep
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.brand_analysis.extract import ExtractedSite, extract_site_signals
from app.modules.brand_analysis.fetch import fetch_website
from app.modules.brand_analysis.provider import get_brand_analysis_result
from app.modules.brand_analysis.schemas import AnalyzeBrandRequest
from app.modules.brand_profiles.resolution import get_own_brand_profile
from app.modules.brand_profiles.service import ALLOWED_IMAGE_MIME_TYPES
from app.modules.companies.onboarding import advance_onboarding_step
from app.modules.creatives.pricing import get_creative_settings
from app.modules.storage.base import StorageService

logger = structlog.get_logger(__name__)

_LOGO_FETCH_TIMEOUT_S = 8.0
_LOGO_MAX_BYTES = 3_000_000


class BrandAnalysisService:
    def __init__(self, session: AsyncSession, audit: AuditService, storage: StorageService) -> None:
        self._session = session
        self._audit = audit
        self._storage = storage

    async def analyze(
        self,
        scope: BrandAnalysisScope,
        owner_id: str,
        actor: User,
        payload: AnalyzeBrandRequest,
    ) -> BrandProfile:
        extracted = ExtractedSite()
        if payload.website_url:
            html = await fetch_website(payload.website_url)
            extracted = extract_site_signals(html, payload.website_url)

        settings = get_creative_settings()
        # Blocking (sync SDK call, or negligible for the mock) -- off the
        # event loop, same convention as SmtpEmailService._send.
        result = await asyncio.to_thread(
            get_brand_analysis_result,
            extracted,
            payload.description,
            settings=settings,
            dry_run=payload.dry_run,
            website_url=payload.website_url,
        )

        profile = await get_own_brand_profile(self._session, scope, owner_id)
        created = profile is None
        if profile is None:
            profile = BrandProfile(
                scope=scope,
                owner_id=owner_id,
                created_by=actor.id,
                category="",
                market="",
                audience_primary="",
                compliance_mandatory_disclaimer="",
                # Column defaults apply at flush, not at construction --
                # set explicitly since this row is read from before its
                # first commit below (visual_identity/social_links merges).
                visual_identity={},
                social_links={},
            )
            self._session.add(profile)

        if result.name:
            profile.name = result.name
        if result.tagline:
            profile.tagline = result.tagline
        if result.description:
            profile.description = result.description
        if result.category:
            profile.category = result.category
        if result.tone:
            profile.tone = result.tone
        if result.audience_primary:
            profile.audience_primary = result.audience_primary
        if result.audience_secondary:
            profile.audience_secondary = result.audience_secondary
        if result.regulatory_category:
            profile.regulatory_category = result.regulatory_category
        if result.palette:
            profile.visual_identity = {**profile.visual_identity, "palette": result.palette}
        if payload.website_url:
            profile.website_url = payload.website_url
        if extracted.contact_email and not profile.contact_email:
            profile.contact_email = extracted.contact_email
        if extracted.contact_number and not profile.contact_number:
            profile.contact_number = extracted.contact_number
        if extracted.social_links:
            profile.social_links = {**extracted.social_links, **profile.social_links}

        if extracted.logo_candidate_url:
            await self._try_download_logo(profile, extracted.logo_candidate_url, scope, owner_id)

        if scope == BrandAnalysisScope.company:
            await advance_onboarding_step(
                self._session, owner_id, CompanyOnboardingStep.brand_profile_completed
            )

        await self._audit.log(
            action="brand_profile.analyzed" if not created else "brand_profile.analyzed_created",
            actor_user_id=actor.id,
            resource_type="brand_profile",
            metadata={"scope": scope.value, "owner_id": owner_id},
        )
        await self._session.commit()
        return profile

    async def _try_download_logo(
        self, profile: BrandProfile, logo_url: str, scope: BrandAnalysisScope, owner_id: str
    ) -> None:
        """Best-effort only -- a failed/ambiguous logo fetch never fails the
        whole analysis. The admin can always upload one manually via
        PUT .../brand-profile/logo."""
        if profile.logo_storage_key is not None:
            return  # don't clobber a manually-uploaded logo
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_LOGO_FETCH_TIMEOUT_S
            ) as client:
                response = await client.get(logo_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                ext = ALLOWED_IMAGE_MIME_TYPES.get(content_type)
                if ext is None or len(response.content) > _LOGO_MAX_BYTES:
                    return
                key = f"{scope.value}/{owner_id}/brand/logo.{ext}"
                await self._storage.save(key=key, data=response.content, content_type=content_type)
                profile.logo_storage_key = key
                profile.logo_mime_type = content_type
        except httpx.HTTPError as exc:
            logger.info("brand_analysis_logo_fetch_failed", url=logo_url, error=str(exc))
