from pydantic import BaseModel, ConfigDict, Field

from app.core.schema_types import UTCDatetime
from app.models.enums import ProductStatus, SubProductBrandingMode


class SubProductPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    name: str
    slug: str
    status: ProductStatus
    branding_mode: SubProductBrandingMode
    created_by: str
    created_at: UTCDatetime
    updated_at: UTCDatetime


class CreateSubProductsRequest(BaseModel):
    """Phase 9's quick-add: names only, one or more at a time ("+ Add
    Another" then "Save and Continue" submits the whole batch in one call)."""

    names: list[str] = Field(min_length=1)


class UpdateSubProductRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: ProductStatus | None = None
    branding_mode: SubProductBrandingMode | None = None
