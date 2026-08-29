from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.mixins import utcnow
from app.models.company import Company
from app.models.company_member import CompanyMember
from app.models.enums import (
    CompanyRole,
    CompanyStatus,
    SocialAccountStatus,
    SocialPlatform,
    UserStatus,
)
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.social_account import SocialAccount
from app.models.user import User
from app.modules.admin.schemas import (
    CompanyDetail,
    CompanyKPIs,
    KPISummary,
    RejectCompanyRequest,
    SocialAccountKPIs,
    SuspendCompanyRequest,
    UserCompanyMembership,
    UserDetail,
    UserKPIs,
    UserListResponse,
    UserProductMembership,
    UserSummary,
)
from app.modules.audit.service import AuditService
from app.modules.companies.service import company_member_to_public
from app.modules.email.base import EmailService
from app.modules.social_accounts.schemas import SocialAccountPublic

MAX_USER_LIST_LIMIT = 200


class AdminService:
    def __init__(self, session: AsyncSession, audit: AuditService, email: EmailService) -> None:
        self._session = session
        self._audit = audit
        self._email = email

    async def list_companies(self, status_filter: CompanyStatus | None) -> list[Company]:
        stmt = select(Company).where(Company.deleted_at.is_(None))
        if status_filter is not None:
            stmt = stmt.where(Company.status == status_filter)
        result = await self._session.scalars(stmt.order_by(Company.created_at.desc()))
        return list(result)

    async def get_company_detail(self, company_id: str) -> CompanyDetail:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")

        member_rows = (
            await self._session.execute(
                select(CompanyMember, User)
                .join(User, User.id == CompanyMember.user_id)
                .where(CompanyMember.company_id == company_id)
                .order_by(CompanyMember.created_at)
            )
        ).all()

        social_rows = await self._session.scalars(
            select(SocialAccount)
            .join(Product, Product.id == SocialAccount.product_id)
            .where(Product.company_id == company_id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at)
        )

        return CompanyDetail(
            id=company.id,
            name=company.name,
            slug=company.slug,
            status=company.status,
            created_at=company.created_at,
            approved_at=company.approved_at,
            rejected_at=company.rejected_at,
            rejection_reason=company.rejection_reason,
            members=[company_member_to_public(member, user) for member, user in member_rows],
            social_accounts=[
                SocialAccountPublic.model_validate(account) for account in social_rows
            ],
        )

    async def list_users(
        self,
        *,
        company_id: str | None = None,
        status_filter: UserStatus | None = None,
        is_super_admin: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> UserListResponse:
        limit = min(max(limit, 1), MAX_USER_LIST_LIMIT)
        offset = max(offset, 0)

        base_stmt = select(User).where(User.deleted_at.is_(None))
        if status_filter is not None:
            base_stmt = base_stmt.where(User.status == status_filter)
        if is_super_admin is not None:
            base_stmt = base_stmt.where(User.is_super_admin == is_super_admin)
        if company_id is not None:
            base_stmt = base_stmt.join(
                CompanyMember, CompanyMember.user_id == User.id
            ).where(CompanyMember.company_id == company_id)

        total = (
            await self._session.execute(
                select(func.count()).select_from(base_stmt.with_only_columns(User.id).subquery())
            )
        ).scalar_one()

        users = list(
            await self._session.scalars(
                base_stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
            )
        )

        user_ids = [user.id for user in users]
        companies_by_user: dict[str, list[UserCompanyMembership]] = defaultdict(list)
        products_by_user: dict[str, list[UserProductMembership]] = defaultdict(list)
        if user_ids:
            membership_rows = (
                await self._session.execute(
                    select(CompanyMember, Company)
                    .join(Company, Company.id == CompanyMember.company_id)
                    .where(CompanyMember.user_id.in_(user_ids))
                    .order_by(Company.created_at.desc())
                )
            ).all()
            for member, company in membership_rows:
                companies_by_user[member.user_id].append(
                    UserCompanyMembership(
                        company_id=company.id,
                        company_name=company.name,
                        company_slug=company.slug,
                        role=member.role,
                        status=member.status,
                        joined_at=member.joined_at,
                    )
                )

            product_rows = (
                await self._session.execute(
                    select(ProductMember, Product)
                    .join(Product, Product.id == ProductMember.product_id)
                    .where(
                        ProductMember.user_id.in_(user_ids), Product.deleted_at.is_(None)
                    )
                    .order_by(Product.created_at.desc())
                )
            ).all()
            for member, product in product_rows:
                products_by_user[member.user_id].append(
                    UserProductMembership(
                        product_id=product.id,
                        product_name=product.name,
                        product_slug=product.slug,
                        company_id=product.company_id,
                        role=member.role,
                    )
                )

        return UserListResponse(
            items=[
                UserSummary(
                    id=user.id,
                    email=user.email,
                    full_name=user.full_name,
                    is_super_admin=user.is_super_admin,
                    status=user.status,
                    email_verified_at=user.email_verified_at,
                    last_login_at=user.last_login_at,
                    created_at=user.created_at,
                    companies=companies_by_user.get(user.id, []),
                    products=products_by_user.get(user.id, []),
                )
                for user in users
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_user_detail(self, user_id: str) -> UserDetail:
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFoundError("User not found.")

        company_rows = (
            await self._session.execute(
                select(CompanyMember, Company)
                .join(Company, Company.id == CompanyMember.company_id)
                .where(CompanyMember.user_id == user_id)
                .order_by(Company.created_at.desc())
            )
        ).all()
        companies = [
            UserCompanyMembership(
                company_id=company.id,
                company_name=company.name,
                company_slug=company.slug,
                role=member.role,
                status=member.status,
                joined_at=member.joined_at,
            )
            for member, company in company_rows
        ]

        product_rows = (
            await self._session.execute(
                select(ProductMember, Product)
                .join(Product, Product.id == ProductMember.product_id)
                .where(ProductMember.user_id == user_id, Product.deleted_at.is_(None))
                .order_by(Product.created_at.desc())
            )
        ).all()
        products = [
            UserProductMembership(
                product_id=product.id,
                product_name=product.name,
                product_slug=product.slug,
                company_id=product.company_id,
                role=member.role,
            )
            for member, product in product_rows
        ]

        social_rows = await self._session.scalars(
            select(SocialAccount)
            .where(SocialAccount.added_by == user_id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at.desc())
        )

        return UserDetail(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_super_admin=user.is_super_admin,
            status=user.status,
            email_verified_at=user.email_verified_at,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            companies=companies,
            products=products,
            social_accounts=[
                SocialAccountPublic.model_validate(account) for account in social_rows
            ],
        )

    async def get_kpis(self) -> KPISummary:
        company_counts = dict(
            (
                await self._session.execute(
                    select(Company.status, func.count())
                    .where(Company.deleted_at.is_(None))
                    .group_by(Company.status)
                )
            ).all()
        )
        companies = CompanyKPIs(
            total=sum(company_counts.values()),
            pending_approval=company_counts.get(CompanyStatus.pending_approval, 0),
            active=company_counts.get(CompanyStatus.active, 0),
            rejected=company_counts.get(CompanyStatus.rejected, 0),
            suspended=company_counts.get(CompanyStatus.suspended, 0),
        )

        user_counts = dict(
            (
                await self._session.execute(
                    select(User.status, func.count())
                    .where(User.deleted_at.is_(None))
                    .group_by(User.status)
                )
            ).all()
        )
        super_admin_total = (
            await self._session.execute(
                select(func.count())
                .select_from(User)
                .where(User.deleted_at.is_(None), User.is_super_admin.is_(True))
            )
        ).scalar_one()
        users = UserKPIs(
            total=sum(user_counts.values()),
            active=user_counts.get(UserStatus.active, 0),
            pending=user_counts.get(UserStatus.pending, 0),
            suspended=user_counts.get(UserStatus.suspended, 0),
            super_admins=super_admin_total,
        )

        social_status_counts = dict(
            (
                await self._session.execute(
                    select(SocialAccount.status, func.count())
                    .where(SocialAccount.deleted_at.is_(None))
                    .group_by(SocialAccount.status)
                )
            ).all()
        )
        social_platform_counts = dict(
            (
                await self._session.execute(
                    select(SocialAccount.platform, func.count())
                    .where(SocialAccount.deleted_at.is_(None))
                    .group_by(SocialAccount.platform)
                )
            ).all()
        )
        social_accounts = SocialAccountKPIs(
            total=sum(social_status_counts.values()),
            active=social_status_counts.get(SocialAccountStatus.active, 0),
            disabled=social_status_counts.get(SocialAccountStatus.disabled, 0),
            by_platform={
                platform: social_platform_counts.get(platform, 0) for platform in SocialPlatform
            },
        )

        return KPISummary(companies=companies, users=users, social_accounts=social_accounts)

    async def _company_admin_emails(self, company_id: str) -> list[str]:
        rows = (
            await self._session.execute(
                select(User.email)
                .join(CompanyMember, CompanyMember.user_id == User.id)
                .where(
                    CompanyMember.company_id == company_id,
                    CompanyMember.role == CompanyRole.company_admin,
                )
            )
        ).all()
        return [row[0] for row in rows]

    async def approve_company(self, company_id: str, actor: User) -> Company:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")
        if company.status == CompanyStatus.active:
            raise ConflictError("Company is already approved.")

        company.status = CompanyStatus.active
        company.approved_by = actor.id
        company.approved_at = utcnow()
        company.rejected_at = None
        company.rejection_reason = None

        await self._audit.log(
            action="company.approved",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
        )
        await self._session.commit()

        for admin_email in await self._company_admin_emails(company_id):
            await self._email.send_company_approved_email(
                to_email=admin_email, company_name=company.name
            )
        return company

    async def reject_company(
        self, company_id: str, actor: User, payload: RejectCompanyRequest
    ) -> Company:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")
        if company.status != CompanyStatus.pending_approval:
            raise ConflictError("Only a company pending approval can be rejected.")

        company.status = CompanyStatus.rejected
        company.rejected_at = utcnow()
        company.rejection_reason = payload.reason

        await self._audit.log(
            action="company.rejected",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
            metadata={"reason": payload.reason},
        )
        await self._session.commit()

        for admin_email in await self._company_admin_emails(company_id):
            await self._email.send_company_rejected_email(
                to_email=admin_email, company_name=company.name, reason=payload.reason
            )
        return company

    async def suspend_company(
        self, company_id: str, actor: User, payload: SuspendCompanyRequest
    ) -> Company:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")
        if company.status == CompanyStatus.suspended:
            raise ConflictError("Company is already suspended.")

        company.status = CompanyStatus.suspended

        await self._audit.log(
            action="company.suspended",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
            metadata={"reason": payload.reason},
        )
        await self._session.commit()

        for admin_email in await self._company_admin_emails(company_id):
            await self._email.send_company_suspended_email(
                to_email=admin_email, company_name=company.name, reason=payload.reason
            )
        return company

    async def delete_company(self, company_id: str, actor: User) -> None:
        company = await self._session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found.")

        admin_emails = await self._company_admin_emails(company_id)
        company_name = company.name
        company.deleted_at = utcnow()

        await self._audit.log(
            action="company.deleted",
            actor_user_id=actor.id,
            company_id=company_id,
            resource_type="company",
            resource_id=company_id,
        )
        await self._session.commit()

        for admin_email in admin_emails:
            await self._email.send_company_deleted_email(
                to_email=admin_email, company_name=company_name
            )
