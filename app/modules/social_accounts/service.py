from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.mixins import utcnow
from app.models.social_account import SocialAccount
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.social_accounts.schemas import AddSocialAccountRequest


class SocialAccountService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def list_accounts(self, product_id: str) -> list[SocialAccount]:
        result = await self._session.scalars(
            select(SocialAccount)
            .where(SocialAccount.product_id == product_id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at)
        )
        return list(result)

    async def add_account(
        self, product_id: str, actor: User, payload: AddSocialAccountRequest
    ) -> SocialAccount:
        existing = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.product_id == product_id,
                SocialAccount.platform == payload.platform,
                SocialAccount.handle == payload.handle,
                SocialAccount.deleted_at.is_(None),
            )
        )
        if existing is not None:
            raise ConflictError("This social account is already connected to the product.")

        account = SocialAccount(
            product_id=product_id,
            platform=payload.platform,
            handle=payload.handle,
            profile_url=payload.profile_url,
            scope=payload.scope,
            sub_product_ids=payload.sub_product_ids,
            added_by=actor.id,
        )
        self._session.add(account)
        await self._audit.log(
            action="social_account.added",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="social_account",
            resource_id=account.id,
            metadata={"platform": payload.platform.value, "handle": payload.handle},
        )
        await self._session.commit()
        return account

    async def remove_account(self, product_id: str, social_account_id: str, actor: User) -> None:
        account = await self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.id == social_account_id,
                SocialAccount.product_id == product_id,
                SocialAccount.deleted_at.is_(None),
            )
        )
        if account is None:
            raise NotFoundError("Social account not found.")

        account.deleted_at = utcnow()
        await self._audit.log(
            action="social_account.removed",
            actor_user_id=actor.id,
            product_id=product_id,
            resource_type="social_account",
            resource_id=social_account_id,
        )
        await self._session.commit()
