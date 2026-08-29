from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import (
    create_access_token,
    generate_opaque_token,
    generate_otp,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.core.slugs import slugify
from app.db.mixins import utcnow
from app.models.company import Company
from app.models.company_member import CompanyMember
from app.models.email_verification_otp import EmailVerificationOtp
from app.models.enums import (
    CompanyMemberStatus,
    CompanyOnboardingStep,
    CompanyRole,
    CompanyStatus,
    RefreshTokenRevokedReason,
    UserStatus,
)
from app.models.password_reset_otp import PasswordResetOtp
from app.models.password_reset_token import PasswordResetToken
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.auth.schemas import (
    CompanyMembershipPublic,
    MeResponse,
    ProductAccessPublic,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from app.modules.auth.token_issuance import issue_token_pair
from app.modules.companies.onboarding import advance_onboarding_step
from app.modules.email.base import EmailService


class AuthService:
    def __init__(self, session: AsyncSession, audit: AuditService, email: EmailService) -> None:
        self._session = session
        self._audit = audit
        self._email = email

    async def _unique_company_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        suffix = 1
        while await self._session.scalar(select(Company.id).where(Company.slug == candidate)):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    def _token_pair(self, *, user: User, raw_refresh_token: str) -> TokenPair:
        settings = get_settings()
        access_token = create_access_token(
            user_id=user.id,
            is_super_admin=user.is_super_admin,
            token_version=user.token_version,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            expires_in=settings.access_token_ttl_minutes * 60,
        )

    async def register(
        self, payload: RegisterRequest, *, ip_address: str | None, user_agent: str | None
    ) -> tuple[User, Company]:
        settings = get_settings()
        email = payload.email.lower()

        existing = await self._session.scalar(select(User.id).where(User.email == email))
        if existing is not None:
            raise ConflictError("An account with this email already exists.")

        company = Company(
            name=payload.company_name,
            slug=await self._unique_company_slug(payload.company_name),
            # Held for Super Admin review before this company's admin can
            # log in — see the approval gate in login() below.
            status=CompanyStatus.pending_approval,
        )
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            status=UserStatus.pending,
        )
        self._session.add_all([company, user])
        await self._session.flush()

        self._session.add(
            CompanyMember(
                company_id=company.id,
                user_id=user.id,
                role=CompanyRole.company_admin,
                status=CompanyMemberStatus.active,
                joined_at=utcnow(),
            )
        )

        otp = generate_otp()
        self._session.add(
            EmailVerificationOtp(
                user_id=user.id,
                otp_hash=hash_opaque_token(otp),
                expires_at=utcnow()
                + timedelta(minutes=settings.email_verification_otp_ttl_minutes),
            )
        )

        await self._audit.log(
            action="auth.register",
            actor_user_id=user.id,
            company_id=company.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._session.commit()

        await self._email.send_verification_otp(to_email=user.email, otp=otp)
        return user, company

    async def login(
        self, email: str, password: str, *, ip_address: str | None, user_agent: str | None
    ) -> TokenPair:
        normalized_email = email.lower()
        user = await self._session.scalar(select(User).where(User.email == normalized_email))

        # Always run a real Argon2 verify (against a fixed dummy hash when
        # there's no user/hash to check) so this branch takes roughly the
        # same time whether or not the account exists.
        password_ok = verify_password(password, user.password_hash if user else None)
        if user is None or user.deleted_at is not None or not password_ok:
            await self._audit.log(
                action="auth.login.failure",
                actor_user_id=user.id if user else None,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"email": normalized_email},
            )
            await self._session.commit()
            raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")

        if user.status == UserStatus.pending:
            await self._audit.log(
                action="auth.login.blocked",
                actor_user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"reason": "email_not_verified"},
            )
            await self._session.commit()
            raise ForbiddenError(
                "Please verify your email before logging in.", code="email_not_verified"
            )
        if user.status == UserStatus.suspended:
            await self._audit.log(
                action="auth.login.blocked",
                actor_user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"reason": "account_suspended"},
            )
            await self._session.commit()
            raise ForbiddenError("This account has been suspended.", code="account_suspended")

        if not user.is_super_admin:
            blocking_reason = await self._blocking_company_status_reason(user.id)
            if blocking_reason is not None:
                code, message, blocked_company_id = blocking_reason
                await self._audit.log(
                    action="auth.login.blocked",
                    actor_user_id=user.id,
                    company_id=blocked_company_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    metadata={"reason": code},
                )
                await self._session.commit()
                raise ForbiddenError(message, code=code)

        user.last_login_at = utcnow()

        token_pair = await issue_token_pair(
            self._session, user, ip_address=ip_address, user_agent=user_agent
        )
        audit_company_id = None
        if not user.is_super_admin:
            (
                token_pair.company_id,
                token_pair.member_id,
                audit_company_id,
            ) = await self._company_login_context(user.id)
        await self._audit.log(
            action="auth.login.success",
            actor_user_id=user.id,
            company_id=audit_company_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._session.commit()
        return token_pair

    async def _company_login_context(
        self, user_id: str
    ) -> tuple[str | None, str | None, str | None]:
        """(company_id, member_id, audit_company_id) for the login response —
        company_id if they're that company's admin, member_id (the CompanyMember
        row's own id) if they're a plain member. audit_company_id is the
        resolved membership's company regardless of role, for audit logging
        only — it never goes in the response. Only the active membership
        matters (login already verified one exists); in practice a user has
        exactly one, same assumption as _blocking_company_status_reason
        above."""
        membership = await self._session.scalar(
            select(CompanyMember)
            .join(Company, Company.id == CompanyMember.company_id)
            .where(CompanyMember.user_id == user_id, Company.status == CompanyStatus.active)
            .order_by(Company.created_at.desc())
        )
        if membership is None:
            return None, None, None
        if membership.role == CompanyRole.company_admin:
            return membership.company_id, None, membership.company_id
        return None, membership.id, membership.company_id

    async def _blocking_company_status_reason(
        self, user_id: str
    ) -> tuple[str, str, str] | None:
        """None if the user belongs to at least one active, non-deleted
        company (the normal case). Otherwise a (code, message, company_id)
        triple describing why login is blocked, taken from their most
        recently created company — in practice a user has exactly one
        company membership at this point (companies are only joined via a
        company that's already active). A soft-deleted company (Super Admin
        delete) keeps whatever `status` it had at deletion time, so deletion
        is checked separately from status — a company can be simultaneously
        `active` and deleted."""
        memberships = (
            await self._session.execute(
                select(Company)
                .join(CompanyMember, CompanyMember.company_id == Company.id)
                .where(CompanyMember.user_id == user_id)
                .order_by(Company.created_at.desc())
            )
        ).scalars().all()

        if not memberships:
            return None
        if any(
            company.status == CompanyStatus.active and company.deleted_at is None
            for company in memberships
        ):
            return None

        company = memberships[0]
        if company.deleted_at is not None:
            return "company_deleted", "Your company has been removed.", company.id
        if company.status == CompanyStatus.pending_approval:
            return (
                "company_pending_approval",
                "Your company is awaiting Super Admin approval.",
                company.id,
            )
        if company.status == CompanyStatus.rejected:
            return (
                "company_rejected",
                "Your company's registration was rejected.",
                company.id,
            )
        return "company_suspended", "Your company has been suspended.", company.id

    async def refresh(
        self, raw_refresh_token: str, *, ip_address: str | None, user_agent: str | None
    ) -> TokenPair:
        token_hash = hash_opaque_token(raw_refresh_token)
        token = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if token is None:
            raise UnauthorizedError("Invalid refresh token.")

        now = utcnow()

        if token.revoked_at is not None:
            # This exact token was already rotated away (or otherwise
            # revoked) and is being presented again — the family may be
            # compromised. Revoke every still-live token in it.
            family_tokens = await self._session.scalars(
                select(RefreshToken).where(
                    RefreshToken.family_id == token.family_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            for member in family_tokens:
                member.revoked_at = now
                member.revoked_reason = RefreshTokenRevokedReason.reuse_detected
            await self._audit.log(
                action="auth.refresh.reuse_detected",
                actor_user_id=token.user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"family_id": token.family_id},
            )
            await self._session.commit()
            raise UnauthorizedError("Refresh token reuse detected; all sessions revoked.")

        if token.expires_at <= now:
            raise UnauthorizedError("Refresh token expired.")

        user = await self._session.get(User, token.user_id)
        if user is None or user.deleted_at is not None or user.status != UserStatus.active:
            raise UnauthorizedError("Invalid refresh token.")

        settings = get_settings()
        raw_new_token = generate_opaque_token()
        new_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_new_token),
            family_id=token.family_id,
            parent_id=token.id,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        token.revoked_at = now
        token.revoked_reason = RefreshTokenRevokedReason.rotated
        self._session.add(new_token)
        await self._session.commit()

        return self._token_pair(user=user, raw_refresh_token=raw_new_token)

    async def logout(self, user: User, raw_refresh_token: str) -> None:
        token_hash = hash_opaque_token(raw_refresh_token)
        token = await self._session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash, RefreshToken.user_id == user.id
            )
        )
        if token is not None and token.revoked_at is None:
            token.revoked_at = utcnow()
            token.revoked_reason = RefreshTokenRevokedReason.logout
            await self._audit.log(action="auth.logout", actor_user_id=user.id)
            await self._session.commit()

    async def logout_all(self, user: User) -> None:
        user.token_version += 1
        now = utcnow()
        active_tokens = await self._session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
            )
        )
        for token in active_tokens:
            token.revoked_at = now
            token.revoked_reason = RefreshTokenRevokedReason.logout_all
        await self._audit.log(action="auth.logout_all", actor_user_id=user.id)
        await self._session.commit()

    async def me(self, user: User) -> MeResponse:
        membership_rows = (
            await self._session.execute(
                select(CompanyMember, Company)
                .join(Company, Company.id == CompanyMember.company_id)
                .where(CompanyMember.user_id == user.id, Company.deleted_at.is_(None))
            )
        ).all()

        companies = [
            CompanyMembershipPublic(
                company_id=company.id,
                company_name=company.name,
                company_slug=company.slug,
                role=member.role,
                status=member.status,
            )
            for member, company in membership_rows
        ]

        # Company admins can access every product in their own company even
        # without an explicit product_members row (see require_product_access
        # in app.core.deps) — reflect that here rather than under-reporting.
        products: list[ProductAccessPublic] = []
        admin_company_ids = [
            member.company_id
            for member, _ in membership_rows
            if member.role == CompanyRole.company_admin
        ]
        if admin_company_ids:
            admin_products = await self._session.scalars(
                select(Product).where(
                    Product.company_id.in_(admin_company_ids), Product.deleted_at.is_(None)
                )
            )
            products.extend(
                ProductAccessPublic(
                    product_id=p.id,
                    product_name=p.name,
                    company_id=p.company_id,
                    role="company_admin",
                )
                for p in admin_products
            )

        seen_product_ids = {p.product_id for p in products}
        explicit_rows = (
            await self._session.execute(
                select(ProductMember, Product)
                .join(Product, Product.id == ProductMember.product_id)
                .where(ProductMember.user_id == user.id, Product.deleted_at.is_(None))
            )
        ).all()
        for member, product in explicit_rows:
            if product.id in seen_product_ids:
                continue
            products.append(
                ProductAccessPublic(
                    product_id=product.id,
                    product_name=product.name,
                    company_id=product.company_id,
                    role=member.role,
                )
            )

        return MeResponse(
            user=UserPublic.model_validate(user), companies=companies, products=products
        )

    async def verify_email(self, email: str, otp: str) -> None:
        settings = get_settings()
        now = utcnow()
        normalized_email = email.lower()
        user = await self._session.scalar(select(User).where(User.email == normalized_email))
        if user is None or user.deleted_at is not None:
            raise BadRequestError("Invalid or expired code.")

        record = await self._session.scalar(
            select(EmailVerificationOtp)
            .where(
                EmailVerificationOtp.user_id == user.id,
                EmailVerificationOtp.used_at.is_(None),
                EmailVerificationOtp.expires_at > now,
            )
            .order_by(EmailVerificationOtp.created_at.desc())
        )
        if record is None or record.attempts >= settings.otp_max_attempts:
            raise BadRequestError("Invalid or expired code.")

        if hash_opaque_token(otp) != record.otp_hash:
            record.attempts += 1
            await self._session.commit()
            raise BadRequestError("Invalid or expired code.")

        record.used_at = now
        if user.status == UserStatus.pending:
            user.status = UserStatus.active
        if user.email_verified_at is None:
            user.email_verified_at = now

        # Advance onboarding for every company this user is company_admin
        # of (in practice exactly one -- see register()'s single
        # CompanyMember insert).
        admin_company_ids = await self._session.scalars(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == user.id, CompanyMember.role == CompanyRole.company_admin
            )
        )
        for company_id in admin_company_ids:
            await advance_onboarding_step(
                self._session, company_id, CompanyOnboardingStep.email_verified
            )

        await self._audit.log(action="auth.email_verified", actor_user_id=user.id)
        await self._session.commit()

    async def resend_verification(self, email: str) -> None:
        # No password is involved here, so there's no argon2-verify device
        # to equalize timing with — no-enumeration is achieved by always
        # doing the same lookup and always returning the same response,
        # regardless of whether an eligible account exists.
        settings = get_settings()
        normalized_email = email.lower()
        user = await self._session.scalar(select(User).where(User.email == normalized_email))

        if user is not None and user.deleted_at is None and user.status == UserStatus.pending:
            otp = generate_otp()
            self._session.add(
                EmailVerificationOtp(
                    user_id=user.id,
                    otp_hash=hash_opaque_token(otp),
                    expires_at=utcnow()
                    + timedelta(minutes=settings.email_verification_otp_ttl_minutes),
                )
            )
            await self._audit.log(action="auth.resend_verification", actor_user_id=user.id)
            await self._session.commit()
            await self._email.send_verification_otp(to_email=user.email, otp=otp)

    async def forgot_password(self, email: str) -> None:
        settings = get_settings()
        normalized_email = email.lower()
        user = await self._session.scalar(select(User).where(User.email == normalized_email))

        if user is not None and user.deleted_at is None:
            otp = generate_otp()
            self._session.add(
                PasswordResetOtp(
                    user_id=user.id,
                    otp_hash=hash_opaque_token(otp),
                    expires_at=utcnow() + timedelta(minutes=settings.otp_ttl_minutes),
                )
            )
            await self._audit.log(action="auth.forgot_password", actor_user_id=user.id)
            await self._session.commit()
            await self._email.send_password_reset_email(to_email=user.email, otp=otp)

    async def verify_reset_otp(self, email: str, otp: str) -> str:
        """Consumes the OTP and issues a short-lived PasswordResetToken —
        the raw value returned here is what /reset-password actually needs.
        Same generic error for a wrong code, an expired/already-used one,
        and a nonexistent email — this endpoint is a poor place to enumerate
        accounts."""
        settings = get_settings()
        now = utcnow()
        normalized_email = email.lower()
        user = await self._session.scalar(select(User).where(User.email == normalized_email))
        if user is None or user.deleted_at is not None:
            raise BadRequestError("Invalid or expired code.")

        record = await self._session.scalar(
            select(PasswordResetOtp)
            .where(
                PasswordResetOtp.user_id == user.id,
                PasswordResetOtp.used_at.is_(None),
                PasswordResetOtp.expires_at > now,
            )
            .order_by(PasswordResetOtp.created_at.desc())
        )
        if record is None or record.attempts >= settings.otp_max_attempts:
            raise BadRequestError("Invalid or expired code.")

        if hash_opaque_token(otp) != record.otp_hash:
            record.attempts += 1
            await self._session.commit()
            raise BadRequestError("Invalid or expired code.")

        record.used_at = now
        raw_reset_token = generate_opaque_token()
        self._session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_opaque_token(raw_reset_token),
                expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
            )
        )
        await self._audit.log(action="auth.otp_verified", actor_user_id=user.id)
        await self._session.commit()
        return raw_reset_token

    async def reset_password(self, raw_token: str, new_password: str) -> None:
        token_hash = hash_opaque_token(raw_token)
        now = utcnow()
        token = await self._session.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        if token is None:
            raise BadRequestError("Invalid or expired reset token.")

        user = await self._session.get(User, token.user_id)
        if user is None:
            raise BadRequestError("Invalid or expired reset token.")

        user.password_hash = hash_password(new_password)
        user.token_version += 1
        token.used_at = now
        await self._revoke_active_refresh_tokens(
            user.id, now, RefreshTokenRevokedReason.password_changed
        )

        await self._audit.log(action="auth.password_reset", actor_user_id=user.id)
        await self._session.commit()

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise UnauthorizedError("Current password is incorrect.", code="invalid_credentials")

        now = utcnow()
        user.password_hash = hash_password(new_password)
        user.token_version += 1
        await self._revoke_active_refresh_tokens(
            user.id, now, RefreshTokenRevokedReason.password_changed
        )

        await self._audit.log(action="auth.password_changed", actor_user_id=user.id)
        await self._session.commit()

    async def _revoke_active_refresh_tokens(
        self, user_id: str, now: datetime, reason: RefreshTokenRevokedReason
    ) -> None:
        active_tokens = await self._session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        for token in active_tokens:
            token.revoked_at = now
            token.revoked_reason = reason
