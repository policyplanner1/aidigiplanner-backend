from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.schema_types import UTCDatetime


class AuditLogPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    actor_user_id: str | None
    actor_name: str | None
    actor_email: str | None
    company_id: str | None
    company_name: str | None
    product_id: str | None
    resource_type: str | None
    resource_id: str | None
    ip_address: str | None
    user_agent: str | None
    audit_metadata: dict[str, Any] | None
    created_at: UTCDatetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogPublic]
    total: int
    limit: int
    offset: int
