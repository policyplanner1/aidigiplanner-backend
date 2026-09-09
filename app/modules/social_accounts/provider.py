from functools import lru_cache

from app.core.config import Settings, get_settings
from app.modules.social_accounts.google import (
    CompositeSocialOAuthClient,
    GoogleBusinessOAuthClient,
    GoogleYouTubeOAuthClient,
)
from app.modules.social_accounts.meta import MetaSocialOAuthClient
from app.modules.social_accounts.oauth import (
    Auth0SocialOAuthClient,
    SocialOAuthClient,
    UnconfiguredSocialOAuthClient,
)


def _instagram_client(settings: Settings) -> SocialOAuthClient:
    meta = MetaSocialOAuthClient(settings)
    if meta.is_configured():
        return meta
    return Auth0SocialOAuthClient(settings)


@lru_cache
def get_social_oauth_client() -> SocialOAuthClient:
    settings = get_settings()
    client = CompositeSocialOAuthClient(
        instagram=_instagram_client(settings),
        youtube=GoogleYouTubeOAuthClient(settings),
        google=GoogleBusinessOAuthClient(settings),
    )
    if client.is_configured():
        return client
    return UnconfiguredSocialOAuthClient()
