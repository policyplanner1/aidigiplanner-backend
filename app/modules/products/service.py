from typing import Any

from sqlalchemy import UnaryExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.slugs import slugify
from app.db.mixins import utcnow
from app.models.company import Company
from app.models.company_member import CompanyMember
from app.models.creative_concept import CreativeConcept
from app.models.enums import (
    CompanyMemberStatus,
    CompanyOnboardingStep,
    CompanyRole,
    ContentStatus,
    GenerationJobStatus,
    ProductStatus,
    SocialAccountStatus,
)
from app.models.generation_job import GenerationJob
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.social_account import SocialAccount
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.companies.onboarding import advance_onboarding_step
from app.modules.companies.provisioning import find_or_create_user_by_email
from app.modules.email.base import EmailService
from app.modules.products.schemas import (
    AddProductMemberRequest,
    ConceptSummary,
    CreateProductRequest,
    DashboardSummary,
    InviteProductMemberRequest,
    ProductMemberPublic,
    UpdateProductRequest,
)


def _member_to_public(member: ProductMember, user: User) -> ProductMemberPublic:
    return ProductMemberPublic(
        id=member.id,
        product_id=member.product_id,
        user_id=member.user_id,
        role=member.role,
        sub_product_ids=member.sub_product_ids,
        added_by=member.added_by,
        created_at=member.created_at,
        user_email=user.email,
        user_full_name=user.full_name,
    )


class ProductService:
    def __init__(self, session: AsyncSession, audit: AuditService, email: EmailService) -> None:
        self._session = session
        self._audit = audit
        self._email = email

    async def _unique_product_slug(
        self, company_id: str, name: str, *, exclude_product_id: str | None = None
    ) -> str:
        base = slugify(name)
        candidate = base
        suffix = 1
        while True:
            stmt = select(Product.id).where(
                Product.company_id == company_id, Product.slug == candidate
            )
            if exclude_product_id is not None:
                stmt = stmt.where(Product.id != exclude_product_id)
            if await self._session.scalar(stmt) is None:
                return candidate
            suffix += 1
            candidate = f"{base}-{suffix}"

    async def list_products(
        self, company_id: str, *, user_id: str, full_access: bool
    ) -> list[Product]:
        if full_access:
            stmt = select(Product).where(
                Product.company_id == company_id, Product.deleted_at.is_(None)
            )
        else:
            stmt = (
                select(Product)
                .join(ProductMember, ProductMember.product_id == Product.id)
                .where(
                    Product.company_id == company_id,
                    Product.deleted_at.is_(None),
                    ProductMember.user_id == user_id,
                )
            )
        result = await self._session.scalars(stmt.order_by(Product.created_at))
        return list(result)

    async def create_product(
        self, company_id: str, actor: User, payload: CreateProductRequest
    ) -> Product:
        product = Product(
            company_id=company_id,
            name=payload.name,
            slug=await self._unique_product_slug(company_id, payload.name),
            description=payload.description,
            status=ProductStatus.active,
            branding_mode=payload.branding_mode,
            created_by=actor.id,
        )
        self._session.add(product)
        await advance_onboarding_step(
            self._session, company_id, CompanyOnboardingStep.first_product_created
        )
        await self._audit.log(
            action="product.created",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="product",
        )
        await self._session.commit()
        return product

    async def update_product(
        self, product_id: str, actor: User, payload: UpdateProductRequest
    ) -> Product:
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found.")

        if payload.name is not None and payload.name != product.name:
            product.name = payload.name
            product.slug = await self._unique_product_slug(
                product.company_id, payload.name, exclude_product_id=product.id
            )
        if payload.description is not None:
            product.description = payload.description
        if payload.status is not None:
            product.status = payload.status
        if payload.branding_mode is not None:
            product.branding_mode = payload.branding_mode
        if payload.approval_policy is not None:
            product.approval_policy = payload.approval_policy

        await self._audit.log(
            action="product.updated",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product_id,
            resource_type="product",
            resource_id=product_id,
        )
        await self._session.commit()
        return product

    async def delete_product(self, product_id: str, actor: User) -> None:
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found.")

        now = utcnow()
        product.deleted_at = now

        # Cascade the soft-delete to the product's social accounts so every
        # read path that already filters on SocialAccount.deleted_at (company
        # detail, user detail, KPIs) stops surfacing them once the parent
        # product is gone, without needing a Product.deleted_at check bolted
        # onto each of those queries too.
        social_accounts = await self._session.scalars(
            select(SocialAccount).where(
                SocialAccount.product_id == product_id, SocialAccount.deleted_at.is_(None)
            )
        )
        for account in social_accounts:
            account.deleted_at = now

        await self._audit.log(
            action="product.deleted",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product_id,
            resource_type="product",
            resource_id=product_id,
        )
        await self._session.commit()

    async def list_members(self, product_id: str) -> list[ProductMemberPublic]:
        rows = (
            await self._session.execute(
                select(ProductMember, User)
                .join(User, User.id == ProductMember.user_id)
                .where(ProductMember.product_id == product_id)
                .order_by(ProductMember.created_at)
            )
        ).all()
        return [_member_to_public(member, user) for member, user in rows]

    async def add_member(
        self, product_id: str, actor: User, payload: AddProductMemberRequest
    ) -> ProductMemberPublic:
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found.")

        target_user = await self._session.get(User, payload.user_id)
        if target_user is None or target_user.deleted_at is not None:
            raise NotFoundError("User not found.")

        company_membership = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == product.company_id,
                CompanyMember.user_id == payload.user_id,
            )
        )
        if company_membership is None:
            raise BadRequestError(
                "User must be a member of the company before being added to a product."
            )

        existing = await self._session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product_id, ProductMember.user_id == payload.user_id
            )
        )
        if existing is not None:
            raise ConflictError("This user is already a member of the product.")

        member = ProductMember(
            product_id=product_id,
            user_id=payload.user_id,
            role=payload.role,
            sub_product_ids=payload.sub_product_ids,
            added_by=actor.id,
        )
        self._session.add(member)
        await self._audit.log(
            action="product_member.added",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product_id,
            resource_type="product_member",
            resource_id=member.id,
        )
        await self._session.commit()
        return _member_to_public(member, target_user)

    async def invite_member(
        self, product_id: str, actor: User, payload: InviteProductMemberRequest
    ) -> ProductMemberPublic:
        """Phase 11's team invite -- by email, creating both the User (if
        new) and its CompanyMember (role=member, if this is their first
        product in the company) alongside the ProductMember itself, in one
        call. Unlike add_member above, the caller never needs an existing
        user_id or a pre-existing company membership."""
        product = await self._session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found.")

        target_user, temporary_password, newly_created_user = await find_or_create_user_by_email(
            self._session, payload.email, payload.full_name
        )

        company_membership = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == product.company_id,
                CompanyMember.user_id == target_user.id,
            )
        )
        if company_membership is None:
            self._session.add(
                CompanyMember(
                    company_id=product.company_id,
                    user_id=target_user.id,
                    role=CompanyRole.member,
                    status=CompanyMemberStatus.active,
                    invited_by=actor.id,
                    joined_at=utcnow(),
                )
            )

        existing_product_member = await self._session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product_id, ProductMember.user_id == target_user.id
            )
        )
        if existing_product_member is not None:
            raise ConflictError("This user is already a member of the product.")

        member = ProductMember(
            product_id=product_id,
            user_id=target_user.id,
            role=payload.role,
            sub_product_ids=payload.sub_product_ids,
            added_by=actor.id,
        )
        self._session.add(member)
        await self._audit.log(
            action="product_member.invited",
            actor_user_id=actor.id,
            company_id=product.company_id,
            product_id=product_id,
            resource_type="product_member",
            resource_id=member.id,
        )
        await self._session.commit()

        if newly_created_user and temporary_password is not None:
            company = await self._session.get(Company, product.company_id)
            await self._email.send_new_member_credentials(
                to_email=target_user.email,
                temporary_password=temporary_password,
                company_name=company.name if company else "",
            )

        return _member_to_public(member, target_user)

    async def remove_member(self, product_id: str, member_id: str, actor: User) -> None:
        member = await self._session.scalar(
            select(ProductMember).where(
                ProductMember.id == member_id, ProductMember.product_id == product_id
            )
        )
        if member is None:
            raise NotFoundError("Product member not found.")

        product = await self._session.get(Product, product_id)

        await self._session.delete(member)
        await self._audit.log(
            action="product_member.removed",
            actor_user_id=actor.id,
            company_id=product.company_id if product else None,
            product_id=product_id,
            resource_type="product_member",
            resource_id=member_id,
        )
        await self._session.commit()

    async def get_dashboard(self, product_id: str) -> DashboardSummary:
        """Phase 13's dashboard cards."""
        concept_status_rows = (
            await self._session.execute(
                select(CreativeConcept.status, func.count())
                .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
                .where(
                    GenerationJob.product_id == product_id,
                    CreativeConcept.deleted_at.is_(None),
                )
                .group_by(CreativeConcept.status)
            )
        ).all()
        concept_counts: dict[ContentStatus, int] = {
            status: count for status, count in concept_status_rows
        }
        failed_jobs = (
            await self._session.execute(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.product_id == product_id,
                    GenerationJob.status == GenerationJobStatus.failed,
                )
            )
        ).scalar_one()

        social_status_rows = (
            await self._session.execute(
                select(SocialAccount.status, func.count())
                .where(
                    SocialAccount.product_id == product_id,
                    SocialAccount.deleted_at.is_(None),
                )
                .group_by(SocialAccount.status)
            )
        ).all()
        social_status_counts: dict[SocialAccountStatus, int] = {
            status: count for status, count in social_status_rows
        }

        drafts = concept_counts.get(ContentStatus.draft, 0)
        pending_approvals = concept_counts.get(ContentStatus.in_review, 0)
        scheduled = concept_counts.get(ContentStatus.scheduled, 0)
        social_accounts_total = sum(social_status_counts.values())

        async def _concepts(
            *, status_filter: ContentStatus, order_by: UnaryExpression[Any], limit: int = 5
        ) -> list[ConceptSummary]:
            rows = (
                await self._session.scalars(
                    select(CreativeConcept)
                    .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
                    .where(
                        GenerationJob.product_id == product_id,
                        CreativeConcept.deleted_at.is_(None),
                        CreativeConcept.status == status_filter,
                    )
                    .order_by(order_by)
                    .limit(limit)
                )
            ).all()
            return [ConceptSummary.model_validate(row) for row in rows]

        recent_rows = (
            await self._session.scalars(
                select(CreativeConcept)
                .join(GenerationJob, GenerationJob.id == CreativeConcept.job_id)
                .where(GenerationJob.product_id == product_id, CreativeConcept.deleted_at.is_(None))
                .order_by(CreativeConcept.updated_at.desc())
                .limit(5)
            )
        ).all()
        recent_content = [ConceptSummary.model_validate(row) for row in recent_rows]

        pending_approvals_list = await _concepts(
            status_filter=ContentStatus.in_review, order_by=CreativeConcept.updated_at.asc()
        )
        upcoming_scheduled = await _concepts(
            status_filter=ContentStatus.scheduled, order_by=CreativeConcept.scheduled_at.asc()
        )
        top_performing = await _concepts(
            status_filter=ContentStatus.published, order_by=CreativeConcept.published_at.desc()
        )

        ai_recommendations: list[str] = []
        if drafts:
            noun = "draft" if drafts == 1 else "drafts"
            ai_recommendations.append(f"{drafts} {noun} waiting to be submitted.")
        if pending_approvals:
            noun = "approval" if pending_approvals == 1 else "approvals"
            ai_recommendations.append(f"{pending_approvals} {noun} need your review.")
        if social_accounts_total == 0:
            ai_recommendations.append("Connect a social account to start publishing.")
        if not top_performing:
            ai_recommendations.append("Nothing published yet -- schedule your first post.")
        else:
            latest_published = top_performing[0].published_at
            if latest_published and (utcnow() - latest_published).days >= 7:
                ai_recommendations.append("No content published in the last 7 days.")

        return DashboardSummary(
            product_id=product_id,
            drafts=drafts,
            pending_approvals=pending_approvals,
            scheduled=scheduled,
            published=concept_counts.get(ContentStatus.published, 0),
            failed_jobs=failed_jobs,
            social_accounts_total=social_accounts_total,
            social_accounts_active=social_status_counts.get(SocialAccountStatus.active, 0),
            recent_content=recent_content,
            pending_approvals_list=pending_approvals_list,
            upcoming_scheduled=upcoming_scheduled,
            top_performing=top_performing,
            ai_recommendations=ai_recommendations,
        )
