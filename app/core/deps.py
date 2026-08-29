from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import InvalidAccessTokenError, decode_access_token
from app.db.session import get_db_session
from app.models.company import Company
from app.models.company_member import CompanyMember
from app.models.enums import CompanyRole, ProductRole, UserStatus
from app.models.product import Product
from app.models.product_member import ProductMember
from app.models.sub_product import SubProduct
from app.models.user import User

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: DbSession,
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise UnauthorizedError("Invalid or expired access token") from exc

    user = await session.get(User, claims["sub"])
    if (
        user is None
        or user.deleted_at is not None
        or user.status != UserStatus.active
        or user.token_version != claims["token_version"]
    ):
        # Covers a deleted/suspended account and a token invalidated by
        # logout-all/password-change (token_version bump) in one check.
        raise UnauthorizedError("Invalid or expired access token")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_super_admin() -> Callable[[User], Awaitable[User]]:
    async def dependency(current_user: CurrentUser) -> User:
        if not current_user.is_super_admin:
            raise ForbiddenError("Super admin access required")
        return current_user

    return dependency


def require_company_role(
    *roles: CompanyRole,
) -> Callable[..., Awaitable[CompanyMember | None]]:
    """Resolution order:
    1. Company missing/soft-deleted -> 404.
    2. Super admin -> pass (no membership row to return).
    3. No membership in this company -> 404 (don't reveal it exists to an outsider).
    4. Membership role not in `roles` (or `roles` empty -> any role allowed) -> 403.
    """

    async def dependency(
        company_id: str,
        current_user: CurrentUser,
        session: DbSession,
    ) -> CompanyMember | None:
        company = await session.get(Company, company_id)
        if company is None or company.deleted_at is not None:
            raise NotFoundError("Company not found")

        if current_user.is_super_admin:
            return None

        membership = await session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == current_user.id,
            )
        )
        if membership is None:
            raise NotFoundError("Company not found")
        if roles and membership.role not in roles:
            raise ForbiddenError("Insufficient company role")
        return membership

    return dependency


def require_product_access(
    *roles: ProductRole,
) -> Callable[..., Awaitable[ProductMember | None]]:
    """Resolution order:
    1. Product missing/soft-deleted -> 404.
    2. Super admin -> pass.
    3. Company admin of the product's company -> pass (every product in their
       own company, regardless of `roles`).
    4. No product_members row -> 404 (covers both a fully unrelated user and
       a same-company `member` never assigned to this product).
    5. Membership role not in `roles` (or `roles` empty -> any role allowed) -> 403.
    """

    async def dependency(
        product_id: str,
        current_user: CurrentUser,
        session: DbSession,
    ) -> ProductMember | None:
        product = await session.get(Product, product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Product not found")

        if current_user.is_super_admin:
            return None

        company_membership = await session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == product.company_id,
                CompanyMember.user_id == current_user.id,
            )
        )
        if company_membership is not None and company_membership.role == CompanyRole.company_admin:
            return None

        product_membership = await session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product_id,
                ProductMember.user_id == current_user.id,
            )
        )
        if product_membership is None:
            raise NotFoundError("Product not found")
        if roles and product_membership.role not in roles:
            raise ForbiddenError("Insufficient product role")
        return product_membership

    return dependency


def require_sub_product_access(
    *roles: ProductRole,
) -> Callable[..., Awaitable[ProductMember | None]]:
    """Same resolution as require_product_access, plus one more check: a
    product_members row with a non-empty `sub_product_ids` only grants
    access to sub-products in that list (Phase 11's "Sub-product Access"
    dropdown) -- an empty list still means "all sub-products", same
    convention as the product-scope case above.

    Depends on the path parameter being named `sub_product_id` -- resolves
    the parent product internally rather than requiring both ids in the URL.
    """

    async def dependency(
        sub_product_id: str,
        current_user: CurrentUser,
        session: DbSession,
    ) -> ProductMember | None:
        sub_product = await session.get(SubProduct, sub_product_id)
        if sub_product is None or sub_product.deleted_at is not None:
            raise NotFoundError("Sub-product not found")
        product = await session.get(Product, sub_product.product_id)
        if product is None or product.deleted_at is not None:
            raise NotFoundError("Sub-product not found")

        if current_user.is_super_admin:
            return None

        company_membership = await session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == product.company_id,
                CompanyMember.user_id == current_user.id,
            )
        )
        if company_membership is not None and company_membership.role == CompanyRole.company_admin:
            return None

        product_membership = await session.scalar(
            select(ProductMember).where(
                ProductMember.product_id == product.id,
                ProductMember.user_id == current_user.id,
            )
        )
        if product_membership is None:
            raise NotFoundError("Sub-product not found")
        if (
            product_membership.sub_product_ids
            and sub_product_id not in product_membership.sub_product_ids
        ):
            raise NotFoundError("Sub-product not found")
        if roles and product_membership.role not in roles:
            raise ForbiddenError("Insufficient product role")
        return product_membership

    return dependency
