from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.enums import CompanyOnboardingStep

# Linear progression (Phase 2-7 in the spec) -- advance_onboarding_step()
# only ever moves a company forward along this list, never backward, so
# callers can invoke it unconditionally at each step without checking
# whether an earlier/later step already happened.
_STEP_ORDER = [
    CompanyOnboardingStep.registered,
    CompanyOnboardingStep.email_verified,
    CompanyOnboardingStep.brand_structure_selected,
    CompanyOnboardingStep.brand_profile_completed,
    CompanyOnboardingStep.first_product_created,
    CompanyOnboardingStep.completed,
]


async def advance_onboarding_step(
    session: AsyncSession, company_id: str, step: CompanyOnboardingStep
) -> None:
    """No-ops if the company is missing/deleted or already at/past `step` --
    every call site (email verification, brand-structure selection, brand
    profile confirm, first product creation) can call this unconditionally.
    Does not commit -- the caller's own commit covers this change too."""
    company = await session.get(Company, company_id)
    if company is None or company.deleted_at is not None:
        return
    if _STEP_ORDER.index(step) > _STEP_ORDER.index(company.onboarding_step):
        company.onboarding_step = step
