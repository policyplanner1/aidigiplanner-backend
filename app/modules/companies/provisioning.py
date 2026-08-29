from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.security import generate_temporary_password, hash_password
from app.db.mixins import utcnow
from app.models.enums import UserStatus
from app.models.user import User


async def find_or_create_user_by_email(
    session: AsyncSession, email: str, full_name: str | None
) -> tuple[User, str | None, bool]:
    """Shared by CompanyMemberService.add_member and
    ProductService.invite_member: finds an existing account by email, or
    creates one on the spot with a generated temporary password (the caller
    is responsible for emailing it -- see send_new_member_credentials).

    Returns (user, temporary_password, newly_created) -- temporary_password
    is None unless newly_created is True.
    """
    normalized_email = email.lower()
    target_user = await session.scalar(select(User).where(User.email == normalized_email))

    if target_user is not None and target_user.deleted_at is not None:
        raise NotFoundError("User not found.")

    if target_user is not None:
        return target_user, None, False

    if not full_name:
        raise BadRequestError("full_name is required to create a new user.")
    temporary_password = generate_temporary_password()
    target_user = User(
        email=normalized_email,
        password_hash=hash_password(temporary_password),
        full_name=full_name,
        status=UserStatus.active,
        email_verified_at=utcnow(),
    )
    session.add(target_user)
    await session.flush()
    return target_user, temporary_password, True
