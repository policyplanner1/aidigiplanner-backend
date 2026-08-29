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
    added_by: str
    created_at: UTCDatetime


class AddSocialAccountRequest(BaseModel):
    platform: SocialPlatform
    handle: str = Field(min_length=1, max_length=255)
    profile_url: str | None = Field(default=None, max_length=500)
    # Phase 10's "Where should this account be available?" choice.
    scope: SocialAccountScope = SocialAccountScope.product
    sub_product_ids: list[str] = Field(default_factory=list)
