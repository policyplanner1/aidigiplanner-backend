from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.db.mixins import utcnow
from app.models.brand_profile import BrandProfile
from app.models.company import Company
from app.models.company_member import CompanyMember
from app.models.enums import (
    BrandAnalysisScope,
    CompanyMemberStatus,
    CompanyOnboardingStep,
    CompanyRole,
)
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.social_account import SocialAccount
from app.models.sub_product import SubProduct
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.brand_profiles.resolution import get_own_brand_profile
from app.modules.brand_profiles.service import ALLOWED_IMAGE_MIME_TYPES
from app.modules.companies.onboarding import advance_onboarding_step
from app.modules.companies.provisioning import find_or_create_user_by_email
from app.modules.companies.schemas import (
    AddCompanyMemberRequest,
    CompanyMemberPublic,
    OnboardingStatus,
    SelectBrandStructureRequest,
    UpdateCompanyMemberRequest,
    UpdateGroupProfileRequest,
    UpdateSingleBrandDetailsRequest,
)
from app.modules.email.base import EmailService
from app.modules.storage.base import StorageService


def company_member_to_public(
    member: CompanyMember, user: User, *, products: list[str] | None = None
) -> CompanyMemberPublic:
    return CompanyMemberPublic(
        id=member.id,
        company_id=member.company_id,
        user_id=member.user_id,
        role=member.role,
        status=member.status,
        invited_by=member.invited_by,
        joined_at=member.joined_at,
        created_at=member.created_at,
        user_email=user.email,
        user_full_name=user.full_name,
        products=products,
    )


class CompanyMemberService:
    def __init__(self, session: AsyncSession, audit: AuditService, email: EmailService) -> None:
        self._session = session
        self._audit = audit
        self._email = email

    async def _active_admin_count(
        self, company_id: str, *, excluding_member_id: str | None = None
    ) -> int:
        rows = await self._session.scalars(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.role == CompanyRole.company_admin,
            )
        )
        return sum(1 for m in rows if m.id != excluding_member_id)

    async def list_members(self, company_id: str) -> list[CompanyMemberPublic]:
        rows = (
            await self._session.execute(
                select(CompanyMember, User)
                .join(User, User.id == CompanyMember.user_id)
                .where(CompanyMember.company_id == company_id)
                .order_by(CompanyMember.created_at)
            )
        ).all()

        product_rows = (
            await self._session.execute(
                select(ProductMember.user_id, Product.name)
                .join(Product, Product.id == ProductMember.product_id)
                .where(Product.company_id == company_id, Product.deleted_at.is_(None))
            )
        ).all()
        products_by_user: dict[str, list[str]] = {}
        for user_id, product_name in product_rows:
            products_by_user.setdefault(user_id, []).append(product_name)

        return [
            company_member_to_public(member, user, products=products_by_user.get(member.user_id))
            for member, user in rows
        ]

    async def add_member(
        self, company_id: str, actor: User, payload: AddCompanyMemberRequest
    ) -> CompanyMemberPublic:
        # No existing account for this email creates one and emails the
        # generated login credentials directly (no self-serve accept step,
        # per the admin-provisions-by-email flow).
        target_user, temporary_password, newly_created = await find_or_create_user_by_email(
            self._session, payload.email, payload.full_name
        )

        if not newly_created:
            existing = await self._session.scalar(
                select(CompanyMember).where(
                    CompanyMember.company_id == company_id,
                    CompanyMember.user_id == target_user.id,
                )
            )
            if existing is not None:
                raise ConflictError("This user is already a member of the company.")

        member = CompanyMember(
            company_id=company_id,
            user_id=target_user.id,
            role=payload.role,
            status=CompanyMemberStatus.active,  # direct-add is immediate, not a pending invite
            invited_by=actor.id,
            joined_at=utcnow(),
        )
        self._session.add(member)
        await self._audit.log(
            action="company_member.added",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company_member",
            resource_id=member.id,
        )
        await self._session.commit()

        if newly_created and temporary_password is not None:
            company = await self._session.get(Company, company_id)
            await self._email.send_new_member_credentials(
                to_email=target_user.email,
                temporary_password=temporary_password,
                company_name=company.name if company else "",
            )

        return company_member_to_public(member, target_user)

    async def update_member(
        self, company_id: str, member_id: str, actor: User, payload: UpdateCompanyMemberRequest
    ) -> CompanyMemberPublic:
        member = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.id == member_id, CompanyMember.company_id == company_id
            )
        )
        if member is None:
            raise NotFoundError("Company member not found.")

        demoting_admin = (
            member.role == CompanyRole.company_admin
            and payload.role is not None
            and payload.role != CompanyRole.company_admin
        )
        if demoting_admin and await self._active_admin_count(
            company_id, excluding_member_id=member.id
        ) == 0:
            raise ConflictError("Cannot remove the last active company admin.")

        if payload.role is not None:
            member.role = payload.role
        if payload.status is not None:
            member.status = payload.status

        target_user = await self._session.get(User, member.user_id)
        assert target_user is not None

        await self._audit.log(
            action="company_member.updated",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company_member",
            resource_id=member.id,
        )
        await self._session.commit()
        return company_member_to_public(member, target_user)

    async def remove_member(self, company_id: str, member_id: str, actor: User) -> None:
        member = await self._session.scalar(
            select(CompanyMember).where(
                CompanyMember.id == member_id, CompanyMember.company_id == company_id
            )
        )
        if member is None:
            raise NotFoundError("Company member not found.")

        if member.role == CompanyRole.company_admin and await self._active_admin_count(
            company_id, excluding_member_id=member.id
        ) == 0:
            raise ConflictError("Cannot remove the last active company admin.")

        await self._session.delete(member)
        await self._audit.log(
            action="company_member.removed",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company_member",
            resource_id=member_id,
        )
        await self._session.commit()


class CompanyService:
    """Onboarding-state operations on the Company row itself (Phase 3-4B,
    12) -- distinct from CompanyMemberService above, which is about who
    belongs to the company, not the company's own setup progress."""

    def __init__(self, session: AsyncSession, audit: AuditService, storage: StorageService) -> None:
        self._session = session
        self._audit = audit
        self._storage = storage

    async def _get_company(self, company_id: str) -> Company:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")
        return company

    async def select_brand_structure(
        self, company_id: str, actor: User, payload: SelectBrandStructureRequest
    ) -> Company:
        company = await self._get_company(company_id)
        company.brand_structure = payload.brand_structure
        await advance_onboarding_step(
            self._session, company_id, CompanyOnboardingStep.brand_structure_selected
        )
        await self._audit.log(
            action="company.brand_structure_selected",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
            metadata={"brand_structure": payload.brand_structure.value},
        )
        await self._session.commit()
        return company

    async def update_single_brand_details(
        self, company_id: str, actor: User, payload: UpdateSingleBrandDetailsRequest
    ) -> Company:
        """Phase 4A's required `industry` dropdown -- stored on the
        company-scope BrandProfile (created empty here if AI analysis
        hasn't run yet), not on the Company row itself."""
        company = await self._get_company(company_id)
        profile = await get_own_brand_profile(self._session, BrandAnalysisScope.company, company_id)
        if profile is None:
            profile = BrandProfile(
                scope=BrandAnalysisScope.company,
                owner_id=company_id,
                created_by=actor.id,
                name=company.name,
                market="",
                audience_primary="",
                compliance_mandatory_disclaimer="",
                visual_identity={},
                social_links={},
                category=payload.industry,
            )
            self._session.add(profile)
        else:
            profile.category = payload.industry

        await self._audit.log(
            action="company.industry_set",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
            metadata={"industry": payload.industry},
        )
        await self._session.commit()
        return company

    async def update_group_profile(
        self, company_id: str, actor: User, payload: UpdateGroupProfileRequest
    ) -> Company:
        company = await self._get_company(company_id)
        if payload.group_website_url is not None:
            company.group_website_url = payload.group_website_url

        await advance_onboarding_step(
            self._session, company_id, CompanyOnboardingStep.brand_profile_completed
        )

        await self._audit.log(
            action="company.group_profile_updated",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
        )
        await self._session.commit()
        return company

    async def upload_group_logo(
        self, company_id: str, actor: User, *, data: bytes, content_type: str
    ) -> Company:
        company = await self._get_company(company_id)
        ext = ALLOWED_IMAGE_MIME_TYPES.get(content_type)
        if ext is None:
            raise BadRequestError(
                f"Unsupported image type {content_type!r}. "
                f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_MIME_TYPES))}."
            )
        key = f"companies/{company_id}/group-logo.{ext}"
        await self._storage.save(key=key, data=data, content_type=content_type)
        company.group_logo_storage_key = key
        company.group_logo_mime_type = content_type

        await self._audit.log(
            action="company.group_logo_uploaded",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
        )
        await self._session.commit()
        return company

    async def complete_onboarding(self, company_id: str, actor: User) -> Company:
        company = await self._get_company(company_id)
        await advance_onboarding_step(
            self._session, company_id, CompanyOnboardingStep.completed
        )
        await self._audit.log(
            action="company.onboarding_completed",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
        )
        await self._session.commit()
        return company

    async def get_onboarding_status(self, company_id: str) -> OnboardingStatus:
        company = await self._get_company(company_id)

        products_count = (
            await self._session.execute(
                select(func.count())
                .select_from(Product)
                .where(Product.company_id == company_id, Product.deleted_at.is_(None))
            )
        ).scalar_one()

        sub_products_count = (
            await self._session.execute(
                select(func.count())
                .select_from(SubProduct)
                .join(Product, Product.id == SubProduct.product_id)
                .where(Product.company_id == company_id, SubProduct.deleted_at.is_(None))
            )
        ).scalar_one()

        social_accounts_count = (
            await self._session.execute(
                select(func.count())
                .select_from(SocialAccount)
                .join(Product, Product.id == SocialAccount.product_id)
                .where(Product.company_id == company_id, SocialAccount.deleted_at.is_(None))
            )
        ).scalar_one()

        team_members_count = (
            await self._session.execute(
                select(func.count())
                .select_from(CompanyMember)
                .where(CompanyMember.company_id == company_id)
            )
        ).scalar_one()

        return OnboardingStatus(
            company_id=company_id,
            onboarding_step=company.onboarding_step,
            brand_structure=company.brand_structure,
            group_website_url=company.group_website_url,
            products_count=products_count,
            sub_products_count=sub_products_count,
            social_accounts_count=social_accounts_count,
            team_members_count=team_members_count,
        )
