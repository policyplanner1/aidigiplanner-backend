from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.factories import login, register_and_login, register_user, verify_user
from tests.fakes import RecordingEmailService


async def _promote_to_super_admin(db_session: AsyncSession, user_id: str) -> None:
    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()


async def test_login_blocked_while_company_pending_approval(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    reg = await register_user(client, "pending-admin@example.com")
    await verify_user(client, email_service, "pending-admin@example.com")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "pending-admin@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "company_pending_approval"
    assert reg["company"]["status"] == "pending_approval"


async def test_super_admin_approve_unblocks_login(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver1@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    reg = await register_user(client, "waiting-admin@example.com")
    await verify_user(client, email_service, "waiting-admin@example.com")
    company_id = reg["company"]["id"]

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "waiting-admin@example.com", "password": "correct-horse-battery"},
    )
    assert blocked.status_code == 403

    approve_resp = await client.post(
        f"/api/v1/admin/companies/{company_id}/approve", headers=super_admin["headers"]
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "active"
    assert email_service.approved_companies["waiting-admin@example.com"] == "Test Co"

    unblocked = await login(client, "waiting-admin@example.com")
    assert unblocked["access_token"]


async def test_super_admin_reject_keeps_login_blocked(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver2@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    reg = await register_user(client, "rejected-admin@example.com")
    await verify_user(client, email_service, "rejected-admin@example.com")
    company_id = reg["company"]["id"]

    reject_resp = await client.post(
        f"/api/v1/admin/companies/{company_id}/reject",
        json={"reason": "Incomplete business details."},
        headers=super_admin["headers"],
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"
    assert email_service.rejected_companies["rejected-admin@example.com"] == (
        "Test Co",
        "Incomplete business details.",
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "rejected-admin@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "company_rejected"


async def test_non_super_admin_forbidden_on_admin_endpoints(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "plain-admin@example.com")

    resp = await client.get("/api/v1/admin/companies", headers=admin["headers"])
    assert resp.status_code == 403


async def test_admin_company_detail_lists_members(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver3@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])
    admin = await register_and_login(client, email_service, "detail-admin@example.com")

    detail_resp = await client.get(
        f"/api/v1/admin/companies/{admin['company_id']}", headers=super_admin["headers"]
    )
    assert detail_resp.status_code == 200
    body = detail_resp.json()
    assert body["status"] == "active"
    assert any(m["user_id"] == admin["user_id"] for m in body["members"])
    assert body["social_accounts"] == []
