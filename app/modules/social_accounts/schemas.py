from pydantic import BaseModel, ConfigDict, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import (
    SocialAccountScope,
    SocialAccountStatus,
    SocialConnectionMethod,
    SocialPlatform,
)


class SocialAccountPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    platform: SocialPlatform
    handle: str
    profile_url: str | None
    status: SocialAccountStatus
    scope: SocialAccountScope
    sub_product_ids: list[str]
    connection_method: SocialConnectionMethod
    external_account_id: str | None = None
    token_expires_at: UTCDatetime | None = None
    added_by: str
    created_at: UTCDatetime


class AddSocialAccountRequest(BaseModel):
    platform: SocialPlatform
    handle: str = Field(min_length=1, max_length=255)
    profile_url: str | None = Field(default=None, max_length=500)
    # Phase 10's "Where should this account be available?" choice.
    scope: SocialAccountScope = SocialAccountScope.product
    sub_product_ids: list[str] = Field(default_factory=list)


class StartSocialOAuthRequest(BaseModel):
    scope: SocialAccountScope = SocialAccountScope.product
    sub_product_ids: list[str] = Field(default_factory=list)
    # Relative SPA path to send the browser to after OAuth returns
    # (e.g. /app/social-accounts or /onboarding/social-accounts).
    return_to: str | None = Field(default=None, max_length=500)
    # SPA origin (http://localhost:5173 or https://aisocialplanner.in).
    return_origin: str | None = Field(default=None, max_length=200)


class StartSocialOAuthResponse(BaseModel):
    authorize_url: str
    # Exact URI Instagram/Facebook must have under Valid OAuth Redirect URIs.
    redirect_uri: str | None = None


class FacebookTestPostResponse(BaseModel):
    platform: SocialPlatform = SocialPlatform.facebook
    photo_id: str
    post_id: str | None = None
    permalink: str | None = None
    caption: str
