from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.user import User
from app.modules.audit.schemas import AuditLogListResponse, AuditLogPublic

MAX_LIST_LIMIT = 200


def _to_public(log: AuditLog, user: User | None, company: Company | None) -> AuditLogPublic:
    return AuditLogPublic(
        id=log.id,
        action=log.action,
        actor_user_id=log.actor_user_id,
        actor_name=user.full_name if user else None,
        actor_email=user.email if user else None,
        company_id=log.company_id,
        company_name=company.name if company else None,
        product_id=log.product_id,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        audit_metadata=log.audit_metadata,
        created_at=log.created_at,
    )


class AuditService:
    """Adds an audit_logs row via flush(), not commit() — callers own the
    transaction boundary so the audit entry lands atomically with whatever
    business change it's recording."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        action: str,
        actor_user_id: str | None = None,
        company_id: str | None = None,
        product_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry = AuditLog(
            actor_user_id=actor_user_id,
            company_id=company_id,
            product_id=product_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            audit_metadata=metadata,
        )
        self._session.add(entry)
        await self._session.flush()

    def _filtered(
        self,
        stmt: Select[Any],
        *,
        action: str | None,
        actor_user_id: str | None,
        company_id: str | None,
        product_id: str | None,
        resource_type: str | None,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> Select[Any]:
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if company_id is not None:
            stmt = stmt.where(AuditLog.company_id == company_id)
        if product_id is not None:
            stmt = stmt.where(AuditLog.product_id == product_id)
        if resource_type is not None:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if from_date is not None:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(AuditLog.created_at <= to_date)
        return stmt

    async def list_logs(
        self,
        *,
        action: str | None = None,
        actor_user_id: str | None = None,
        company_id: str | None = None,
        product_id: str | None = None,
        resource_type: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditLogListResponse:
        limit = min(max(limit, 1), MAX_LIST_LIMIT)
        offset = max(offset, 0)

        count_stmt = self._filtered(
            select(func.count()).select_from(AuditLog),
            action=action,
            actor_user_id=actor_user_id,
            company_id=company_id,
            product_id=product_id,
            resource_type=resource_type,
            from_date=from_date,
            to_date=to_date,
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        rows_stmt = self._filtered(
            select(AuditLog, User, Company)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .outerjoin(Company, Company.id == AuditLog.company_id),
            action=action,
            actor_user_id=actor_user_id,
            company_id=company_id,
            product_id=product_id,
            resource_type=resource_type,
            from_date=from_date,
            to_date=to_date,
        )
        rows = (
            await self._session.execute(
                rows_stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()

        return AuditLogListResponse(
            items=[_to_public(log, user, company) for log, user, company in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_log(self, log_id: str) -> AuditLogPublic:
        row = (
            await self._session.execute(
                select(AuditLog, User, Company)
                .outerjoin(User, User.id == AuditLog.actor_user_id)
                .outerjoin(Company, Company.id == AuditLog.company_id)
                .where(AuditLog.id == log_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Audit log not found.")
        log, user, company = row
        return _to_public(log, user, company)
