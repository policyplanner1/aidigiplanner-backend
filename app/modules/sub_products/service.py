from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.slugs import slugify
from app.db.mixins import utcnow
from app.models.enums import SubProductBrandingMode
from app.models.sub_product import SubProduct
from app.models.user import User
from app.modules.audit.service import AuditService
from app.modules.sub_products.schemas import CreateSubProductsRequest, UpdateSubProductRequest


class SubProductService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def _unique_sub_product_slug(
        self, product_id: str, name: str, *, exclude_sub_product_id: str | None = None
    ) -> str:
        base = slugify(name)
        candidate = base
        suffix = 1
        while True:
            stmt = select(SubProduct.id).where(
                SubProduct.product_id == product_id, SubProduct.slug == candidate
            )
            if exclude_sub_product_id is not None:
                stmt = stmt.where(SubProduct.id != exclude_sub_product_id)
            if await self._session.scalar(stmt) is None:
                return candidate
            suffix += 1
            candidate = f"{base}-{suffix}"

    async def list_sub_products(self, product_id: str) -> list[SubProduct]:
        result = await self._session.scalars(
            select(SubProduct)
            .where(SubProduct.product_id == product_id, SubProduct.deleted_at.is_(None))
            .order_by(SubProduct.created_at)
        )
        return list(result)

    async def create_sub_products(
        self, product_id: str, actor: User, payload: CreateSubProductsRequest
    ) -> list[SubProduct]:
        created: list[SubProduct] = []
        for name in payload.names:
            name = name.strip()
            if not name:
                continue
            sub_product = SubProduct(
                product_id=product_id,
                name=name,
                slug=await self._unique_sub_product_slug(product_id, name),
                # Sub-products inherit the parent product's branding/tone/
                # audience/CTA/compliance by default (Phase 9) -- the admin
                # opts one out into `separate_brand` later via update.
                branding_mode=SubProductBrandingMode.use_product_branding,
                created_by=actor.id,
            )
            self._session.add(sub_product)
            created.append(sub_product)

        if created:
            await self._session.flush()
            await self._audit.log(
                action="sub_product.created",
                actor_user_id=actor.id,
                product_id=product_id,
                resource_type="sub_product",
                metadata={"count": len(created)},
            )
            await self._session.commit()
        return created

    async def update_sub_product(
        self, sub_product_id: str, actor: User, payload: UpdateSubProductRequest
    ) -> SubProduct:
        sub_product = await self._session.get(SubProduct, sub_product_id)
        if sub_product is None or sub_product.deleted_at is not None:
            raise NotFoundError("Sub-product not found.")

        if payload.name is not None and payload.name != sub_product.name:
            sub_product.name = payload.name
            sub_product.slug = await self._unique_sub_product_slug(
                sub_product.product_id, payload.name, exclude_sub_product_id=sub_product.id
            )
        if payload.status is not None:
            sub_product.status = payload.status
        if payload.branding_mode is not None:
            sub_product.branding_mode = payload.branding_mode

        await self._audit.log(
            action="sub_product.updated",
            actor_user_id=actor.id,
            product_id=sub_product.product_id,
            resource_type="sub_product",
            resource_id=sub_product_id,
        )
        await self._session.commit()
        return sub_product

    async def delete_sub_product(self, sub_product_id: str, actor: User) -> None:
        sub_product = await self._session.get(SubProduct, sub_product_id)
        if sub_product is None or sub_product.deleted_at is not None:
            raise NotFoundError("Sub-product not found.")

        sub_product.deleted_at = utcnow()

        await self._audit.log(
            action="sub_product.deleted",
            actor_user_id=actor.id,
            product_id=sub_product.product_id,
            resource_type="sub_product",
            resource_id=sub_product_id,
        )
        await self._session.commit()
