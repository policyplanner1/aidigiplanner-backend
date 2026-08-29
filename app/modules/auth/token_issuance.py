from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, generate_opaque_token, hash_opaque_token
from app.db.mixins import new_uuid7, utcnow
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.modules.auth.schemas import TokenPair


async def issue_token_pair(
    session: AsyncSession, user: User, *, ip_address: str | None, user_agent: str | None
) -> TokenPair:
    """Starts a brand-new refresh-token family for `user` (a fresh login-style
    session) — used by login and by invitation-accept, which both need to
    hand back a working session immediately. Refresh-token *rotation* (an
    existing family continuing) stays in AuthService.refresh(), since that
    needs to preserve family_id/parent_id, not start a new family."""
    settings = get_settings()
    raw_refresh = generate_opaque_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_refresh),
            family_id=new_uuid7(),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    access_token = create_access_token(
        user_id=user.id, is_super_admin=user.is_super_admin, token_version=user.token_version
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
    )
