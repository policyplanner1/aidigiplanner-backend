from datetime import datetime

from arq import ArqRedis
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.db.mixins import utcnow
from app.models.company_member import CompanyMember
from app.models.content_comment import ContentComment
from app.models.creative_asset import CreativeAsset
from app.models.creative_brief import CreativeBrief
from app.models.creative_concept import CreativeConcept
from app.models.enums import (
    CompanyRole,
    ContentApprovalPolicy,
    ContentStatus,
    GenerationJobStatus,
    ProductRole,
    ReelStyle,
)
from app.models.generation_job import GenerationJob
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.brand_profiles.resolution import resolve_effective_brand_profile
from app.modules.creatives.brand import brand_profile_from_row
from app.modules.creatives.domain import Brief
from app.modules.creatives.pipeline.ideate import IDEATE_PROMPT_VERSION
from app.modules.creatives.pricing import estimate_brief_cost, get_creative_settings
from app.modules.creatives.schemas import GenerateCreativesRequest


class CreativeService:
    def __init__(self, session: AsyncSession, audit: AuditService, arq_pool: ArqRedis) -> None:
        self._session = session
        self._audit = audit
        self._arq = arq_pool

    async def _get_existing_job_for_key(
        self, product_id: str, job_key: str
    ) -> GenerationJob | None:
        brief = await self._session.scalar(
            select(CreativeBrief)
            .where(CreativeBrief.product_id == product_id, CreativeBrief.job_key == job_key)
            .order_by(CreativeBrief.created_at.desc())
        )
        if brief is None:
            return None
        job: GenerationJob | None = await self._session.scalar(
            select(GenerationJob)
            .where(GenerationJob.brief_id == brief.id)
            .order_by(GenerationJob.created_at.desc())
        )
        return job

    async def request_generation(
        self,
        product_id: str,
        actor: User,
        payload: GenerateCreativesRequest,
    ) -> GenerationJob:
        product = await self._session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        brand_row = await resolve_effective_brand_profile(self._session, product_id=product_id)
        brand = brand_profile_from_row(brand_row)

        try:
            brief = Brief(
                product_line=payload.product_line,
                topic=payload.topic,
                format=payload.format,
                carousel_slides=payload.carousel_slides,
                reel_duration_s=payload.reel_duration_s,
                language=payload.language,
                concept_count=payload.concept_count,
                quality=payload.quality,
                extra_notes=payload.extra_notes,
                voiceover=payload.voiceover,
                reel_style=payload.reel_style,
                objective=payload.objective,
                offer=payload.offer,
                festival_occasion=payload.festival_occasion,
                audience_override=payload.audience_override,
                tone_override=payload.tone_override,
                cta_override=payload.cta_override,
            )
        except ValidationError as exc:
            raise BadRequestError(f"Invalid brief: {exc.errors()[0]['msg']}") from exc

        try:
            brand.product_line(brief.product_line)
        except KeyError as exc:
            raise BadRequestError(
                f"Unknown product line {brief.product_line!r} for this brand profile."
            ) from exc

        if brief.reel_style == ReelStyle.avatar and brand_row.avatar_storage_key is None:
            raise BadRequestError(
                "This product has no avatar image uploaded yet. Upload one via "
                "PUT .../brand-profile/avatar before generating avatar-style reels."
            )

        job_key = brief.job_key(IDEATE_PROMPT_VERSION)
        if not payload.force:
            existing_job = await self._get_existing_job_for_key(product_id, job_key)
            if existing_job is not None:
                return existing_job

        creative_settings = get_creative_settings()
        estimate = estimate_brief_cost(brief, creative_settings)

        if estimate.total_inr > creative_settings.max_cost_per_run_inr:
            raise BadRequestError(
                f"Estimated cost ₹{estimate.total_inr:.2f} exceeds the per-run cap of "
                f"₹{creative_settings.max_cost_per_run_inr:.2f}."
            )

        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        spend_expr = func.coalesce(GenerationJob.actual_cost_inr, GenerationJob.estimated_cost_inr)
        spent_today = await self._session.scalar(
            select(func.sum(spend_expr)).where(
                GenerationJob.company_id == product.company_id,
                GenerationJob.created_at >= today_start,
            )
        )
        spent_today = float(spent_today or 0)
        if spent_today + estimate.total_inr > creative_settings.max_cost_per_day_inr:
            raise BadRequestError(
                f"This would bring today's spend to ₹{spent_today + estimate.total_inr:.2f}, "
                f"over the per-day cap of ₹{creative_settings.max_cost_per_day_inr:.2f}."
            )

        brief_row = CreativeBrief(
            product_id=product_id,
            product_line=brief.product_line,
            topic=brief.topic,
            format=brief.format,
            carousel_slides=brief.carousel_slides,
            reel_duration_s=brief.reel_duration_s,
            language=brief.language,
            concept_count=brief.concept_count,
            quality=brief.quality,
            extra_notes=brief.extra_notes,
            voiceover=brief.voiceover,
            reel_style=brief.reel_style,
            platforms=[p.value for p in payload.platforms],
            sub_product_id=payload.sub_product_id,
            objective=brief.objective,
            offer=brief.offer,
            festival_occasion=brief.festival_occasion,
            audience_override=brief.audience_override,
            tone_override=brief.tone_override,
            cta_override=brief.cta_override,
            reference_image_storage_key=payload.reference_image_storage_key,
            publishing_date=payload.publishing_date,
            job_key=job_key,
            created_by=actor.id,
        )
        self._session.add(brief_row)
        await self._session.flush()

        job = GenerationJob(
            brief_id=brief_row.id,
            product_id=product_id,
            company_id=product.company_id,
            status=GenerationJobStatus.queued,
            prompt_version=IDEATE_PROMPT_VERSION,
            dry_run=payload.dry_run,
            estimated_cost_inr=estimate.total_inr,
            requested_by=actor.id,
        )
        self._session.add(job)
        await self._audit.log(
            action="creative_job.queued",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product_id,
            resource_type="generation_job",
            metadata={"estimated_cost_inr": estimate.total_inr, "dry_run": payload.dry_run},
        )
        await self._session.commit()

        arq_job = await self._arq.enqueue_job("generate_creatives_job", job.id)
        if arq_job is not None:
            job.arq_job_id = arq_job.job_id
            await self._session.commit()

        return job

    async def render_assets(self, product_id: str, job_id: str, actor: User) -> GenerationJob:
        """Triggers asset rendering for a reel job that finished ideation
        and is sitting at awaiting_render (see the two-step reel flow in
        worker.py). Rejects a post/carousel job (which never reaches
        awaiting_render -- it renders in the same call as generate) and a
        second call on an already-rendered reel job with the same 400,
        since both simply never satisfy the status check below."""
        job = await self.get_job(product_id, job_id)
        if job.status != GenerationJobStatus.awaiting_render:
            raise BadRequestError(
                "This job is not awaiting render. Only a reel job whose ideation has "
                "finished (status 'awaiting_render') can have its assets rendered."
            )

        brief_row = await self._session.get(CreativeBrief, job.brief_id)
        if brief_row is None:
            raise NotFoundError("Creative brief not found.")
        product = await self._session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        brief = Brief(
            product_line=brief_row.product_line,
            topic=brief_row.topic,
            format=brief_row.format,
            carousel_slides=brief_row.carousel_slides,
            reel_duration_s=brief_row.reel_duration_s,
            language=brief_row.language,
            concept_count=brief_row.concept_count,
            quality=brief_row.quality,
            extra_notes=brief_row.extra_notes,
            voiceover=brief_row.voiceover,
            reel_style=brief_row.reel_style,
            objective=brief_row.objective,
            offer=brief_row.offer,
            festival_occasion=brief_row.festival_occasion,
            audience_override=brief_row.audience_override,
            tone_override=brief_row.tone_override,
            cta_override=brief_row.cta_override,
        )
        creative_settings = get_creative_settings()
        estimate = estimate_brief_cost(brief, creative_settings)

        if estimate.total_inr > creative_settings.max_cost_per_run_inr:
            raise BadRequestError(
                f"Estimated cost ₹{estimate.total_inr:.2f} exceeds the per-run cap of "
                f"₹{creative_settings.max_cost_per_run_inr:.2f}."
            )

        # Time may have passed since the original estimate at request time,
        # so re-check the per-day cap against today's spend from every
        # *other* job -- this job's own already-counted estimated_cost_inr
        # is excluded so its (unchanged) projected cost isn't double
        # counted against the cap it already passed once.
        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        spend_expr = func.coalesce(GenerationJob.actual_cost_inr, GenerationJob.estimated_cost_inr)
        spent_today = await self._session.scalar(
            select(func.sum(spend_expr)).where(
                GenerationJob.company_id == product.company_id,
                GenerationJob.created_at >= today_start,
                GenerationJob.id != job.id,
            )
        )
        spent_today = float(spent_today or 0)
        if spent_today + estimate.total_inr > creative_settings.max_cost_per_day_inr:
            raise BadRequestError(
                f"This would bring today's spend to ₹{spent_today + estimate.total_inr:.2f}, "
                f"over the per-day cap of ₹{creative_settings.max_cost_per_day_inr:.2f}."
            )

        await self._audit.log(
            action="creative_job.render_requested",
            actor_user_id=actor.id,
            company_id=job.company_id,
            product_id=product_id,
            resource_type="generation_job",
            resource_id=job.id,
        )
        await self._session.commit()

        arq_job = await self._arq.enqueue_job("render_creative_assets_job", job.id)
        if arq_job is not None:
            job.arq_job_id = arq_job.job_id
            await self._session.commit()

        return job

    async def get_job(self, product_id: str, job_id: str) -> GenerationJob:
        job = await self._session.scalar(
            select(GenerationJob).where(
                GenerationJob.id == job_id, GenerationJob.product_id == product_id
            )
        )
        if job is None:
            raise NotFoundError("Generation job not found.")
        return job

    async def list_concepts(
        self,
        product_id: str,
        *,
        job_id: str | None = None,
        status: ContentStatus | None = None,
    ) -> list[CreativeConcept]:
        stmt = (
            select(CreativeConcept)
            .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
            .where(GenerationJob.product_id == product_id, CreativeConcept.deleted_at.is_(None))
            .order_by(GenerationJob.created_at, CreativeConcept.concept_index)
        )
        if job_id is not None:
            stmt = stmt.where(CreativeConcept.job_id == job_id)
        if status is not None:
            stmt = stmt.where(CreativeConcept.status == status)
        result = await self._session.scalars(stmt)
        return list(result)

    async def _get_concept(self, product_id: str, concept_id: str) -> CreativeConcept:
        concept = await self._session.scalar(
            select(CreativeConcept)
            .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
            .where(
                CreativeConcept.id == concept_id,
                GenerationJob.product_id == product_id,
                CreativeConcept.deleted_at.is_(None),
            )
        )
        if concept is None:
            raise NotFoundError("Creative concept not found.")
        return concept

    async def get_concept(self, product_id: str, concept_id: str) -> CreativeConcept:
        return await self._get_concept(product_id, concept_id)

    async def assets_by_concept_id(self, concept_ids: list[str]) -> dict[str, list[CreativeAsset]]:
        if not concept_ids:
            return {}
        rows = await self._session.scalars(
            select(CreativeAsset)
            .where(CreativeAsset.concept_id.in_(concept_ids), CreativeAsset.deleted_at.is_(None))
            .order_by(CreativeAsset.concept_id, CreativeAsset.label)
        )
        grouped: dict[str, list[CreativeAsset]] = {}
        for asset in rows:
            grouped.setdefault(asset.concept_id, []).append(asset)
        return grouped

    async def get_asset_for_download(self, product_id: str, asset_id: str) -> CreativeAsset:
        asset = await self._session.scalar(
            select(CreativeAsset)
            .join(CreativeConcept, CreativeConcept.id == CreativeAsset.concept_id)
            .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
            .where(
                CreativeAsset.id == asset_id,
                GenerationJob.product_id == product_id,
                CreativeAsset.deleted_at.is_(None),
            )
        )
        if asset is None:
            raise NotFoundError("Creative asset not found.")
        return asset

    async def _get_product(self, product_id: str) -> Product:
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found.")
        return product

    async def _is_company_admin(self, company_id: str, actor: User) -> bool:
        if actor.is_super_admin:
            return True
        membership = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id, CompanyMember.user_id == actor.id
            )
        )
        return membership is not None and membership.role == CompanyRole.company_admin

    async def _product_role(self, product_id: str, actor: User) -> ProductRole | None:
        membership = await self._session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product_id, ProductMember.user_id == actor.id
            )
        )
        return membership.role if membership else None

    async def _require_approval_authority(self, product: Product, actor: User) -> None:
        """Phase 20's Company-Admin-configurable approval policy -- a
        Company Admin always has authority regardless of policy (same
        bypass require_product_access already grants everywhere else)."""
        if await self._is_company_admin(product.company_id, actor):
            return
        policy = product.approval_policy
        if policy == ContentApprovalPolicy.no_approval:
            return
        if policy == ContentApprovalPolicy.company_admin_approval:
            raise ForbiddenError("Only a Company Admin can approve content for this product.")

        role = await self._product_role(product.id, actor)
        if policy == ContentApprovalPolicy.product_manager_approval:
            if role != ProductRole.product_manager:
                raise ForbiddenError(
                    "Only a Product Manager can approve content for this product."
                )
            return
        if policy == ContentApprovalPolicy.one_approver and role not in (
            ProductRole.approver,
            ProductRole.product_manager,
        ):
            raise ForbiddenError(
                "Only an Approver or Product Manager can approve content for this product."
            )

    async def submit_for_review(
        self, product_id: str, concept_id: str, actor: User
    ) -> CreativeConcept:
        concept = await self._get_concept(product_id, concept_id)
        concept.status = ContentStatus.in_review
        concept.reviewed_by = actor.id
        concept.reviewed_at = utcnow()
        await self._audit.log(
            action="creative_concept.submitted_for_review",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
        )
        await self._session.commit()
        return concept

    async def approve_concept(
        self, product_id: str, concept_id: str, actor: User
    ) -> CreativeConcept:
        product = await self._get_product(product_id)
        await self._require_approval_authority(product, actor)
        concept = await self._get_concept(product_id, concept_id)
        concept.status = ContentStatus.approved
        concept.reviewed_by = actor.id
        concept.reviewed_at = utcnow()
        await self._audit.log(
            action="creative_concept.approved",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
        )
        await self._session.commit()
        return concept

    async def reject_concept(
        self, product_id: str, concept_id: str, actor: User, reason: str
    ) -> CreativeConcept:
        """Phase 20's "Request Changes" action -- rejects and files the
        reason as a ContentComment too, so it shows up in the same thread
        as any other reviewer feedback on this concept."""
        product = await self._get_product(product_id)
        await self._require_approval_authority(product, actor)
        concept = await self._get_concept(product_id, concept_id)
        concept.status = ContentStatus.rejected
        concept.reviewed_by = actor.id
        concept.reviewed_at = utcnow()
        self._session.add(ContentComment(concept_id=concept_id, author_id=actor.id, body=reason))
        await self._audit.log(
            action="creative_concept.rejected",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
            metadata={"reason": reason},
        )
        await self._session.commit()
        return concept

    async def schedule_concept(
        self, product_id: str, concept_id: str, actor: User, scheduled_at: datetime
    ) -> CreativeConcept:
        concept = await self._get_concept(product_id, concept_id)
        if concept.status != ContentStatus.approved:
            raise BadRequestError("Only an approved concept can be scheduled.")
        concept.status = ContentStatus.scheduled
        concept.scheduled_at = scheduled_at
        concept.reviewed_by = actor.id
        concept.reviewed_at = utcnow()
        await self._audit.log(
            action="creative_concept.scheduled",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
        )
        await self._session.commit()
        return concept

    async def publish_concept(
        self, product_id: str, concept_id: str, actor: User
    ) -> CreativeConcept:
        """Marks the concept published. Per the "manual entry" social
        scope decision, this records intent/status only -- it never calls
        out to Instagram/Facebook/etc."""
        concept = await self._get_concept(product_id, concept_id)
        if concept.status not in (ContentStatus.approved, ContentStatus.scheduled):
            raise BadRequestError("Only an approved or scheduled concept can be published.")
        now = utcnow()
        concept.status = ContentStatus.published
        concept.published_at = now
        concept.reviewed_by = actor.id
        concept.reviewed_at = now
        await self._audit.log(
            action="creative_concept.published",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
        )
        await self._session.commit()
        return concept

    async def approve_and_publish_concept(
        self, product_id: str, concept_id: str, actor: User
    ) -> CreativeConcept:
        """Phase 20's Company-Admin shortcut: "Approve and publish
        directly" in one call, still subject to approval_policy unless the
        actor is a Company Admin."""
        product = await self._get_product(product_id)
        await self._require_approval_authority(product, actor)
        concept = await self._get_concept(product_id, concept_id)
        now = utcnow()
        concept.status = ContentStatus.published
        concept.published_at = now
        concept.reviewed_by = actor.id
        concept.reviewed_at = now
        await self._audit.log(
            action="creative_concept.approved_and_published",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="creative_concept",
            resource_id=concept_id,
        )
        await self._session.commit()
        return concept

    async def list_comments(self, product_id: str, concept_id: str) -> list[ContentComment]:
        await self._get_concept(product_id, concept_id)  # 404s if out of scope
        result = await self._session.scalars(
            select(ContentComment)
            .where(ContentComment.concept_id == concept_id)
            .order_by(ContentComment.created_at)
        )
        return list(result)

    async def add_comment(
        self, product_id: str, concept_id: str, actor: User, body: str
    ) -> ContentComment:
        await self._get_concept(product_id, concept_id)  # 404s if out of scope
        comment = ContentComment(concept_id=concept_id, author_id=actor.id, body=body)
        self._session.add(comment)
        await self._audit.log(
            action="creative_concept.comment_added",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="content_comment",
        )
        await self._session.commit()
        return comment
