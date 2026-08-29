from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.brand_profile import BrandProfile
from app.models.enums import BrandAnalysisScope, ProductBrandingMode, SubProductBrandingMode
from app.models.product import Product
from app.models.sub_product import SubProduct


async def get_own_brand_profile(
    session: AsyncSession, scope: BrandAnalysisScope, owner_id: str
) -> BrandProfile | None:
    """The raw row at this exact (scope, owner_id) -- no inheritance. None if
    that owner has never had one set (e.g. a product on `separate_brand`
    that hasn't been analyzed/filled in yet)."""
    profile: BrandProfile | None = await session.scalar(
        select(BrandProfile).where(BrandProfile.scope == scope, BrandProfile.owner_id == owner_id)
    )
    return profile


async def resolve_effective_brand_profile(
    session: AsyncSession, *, product_id: str, sub_product_id: str | None = None
) -> BrandProfile:
    """The brand profile actually applied to a product or sub-product,
    walking sub-product -> product -> company per each level's branding
    mode (Phase 4A/4B/8/9/14). Raises BadRequestError if nothing resolves --
    the caller (e.g. CreativeService.request_generation) can't proceed
    without one."""
    if sub_product_id is not None:
        sub_product = await session.get(SubProduct, sub_product_id)
        if sub_product is None or sub_product.deleted_at is not None:
            raise NotFoundError("Sub-product not found.")
        if sub_product.branding_mode == SubProductBrandingMode.separate_brand:
            own = await get_own_brand_profile(
                session, BrandAnalysisScope.sub_product, sub_product_id
            )
            if own is not None:
                return own
        # use_product_branding, or separate_brand not filled in yet -- fall
        # through to the parent product.

    product = await session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise NotFoundError("Product not found.")
    if product.branding_mode == ProductBrandingMode.separate_brand:
        own = await get_own_brand_profile(session, BrandAnalysisScope.product, product_id)
        if own is not None:
            return own

    company_profile = await get_own_brand_profile(
        session, BrandAnalysisScope.company, product.company_id
    )
    if company_profile is not None:
        return company_profile

    raise BadRequestError(
        "No brand profile is set for this product yet. Set one (or the company's) before "
        "generating creatives."
    )
