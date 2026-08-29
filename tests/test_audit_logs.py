from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def _promote_to_super_admin(db_session: AsyncSession, user_id: str) -> None:
    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()


async def test_non_super_admin_forbidden_on_audit_endpoints(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "plain-admin@example.com")

    resp = await client.get("/api/v1/audit/logs", headers=admin["headers"])
    assert resp.status_code == 403


async def test_super_admin_lists_audit_logs_from_company_approval(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "waiting-admin@example.com",
            "password": "correct-horse-battery",
            "full_name": "Waiting Admin",
            "company_name": "Waiting Co",
        },
    )
    assert reg.status_code == 201
    company_id = reg.json()["company"]["id"]

    approve_resp = await client.post(
        f"/api/v1/admin/companies/{company_id}/approve", headers=super_admin["headers"]
    )
    assert approve_resp.status_code == 200

    logs_resp = await client.get("/api/v1/audit/logs", headers=super_admin["headers"])
    assert logs_resp.status_code == 200
    body = logs_resp.json()
    assert body["total"] >= 1
    assert body["limit"] == 50
    assert body["offset"] == 0

    entry = next(item for item in body["items"] if item["action"] == "company.approved")
    assert entry["company_id"] == company_id
    assert entry["company_name"] == "Waiting Co"
    assert entry["actor_user_id"] == super_admin["user_id"]
    assert entry["actor_email"] == "approver@example.com"

    filtered_resp = await client.get(
        "/api/v1/audit/logs",
        params={"action": "company.approved", "company_id": company_id},
        headers=super_admin["headers"],
    )
    assert filtered_resp.status_code == 200
    filtered_body = filtered_resp.json()
    assert filtered_body["total"] == 1
    assert filtered_body["items"][0]["id"] == entry["id"]

    detail_resp = await client.get(
        f"/api/v1/audit/logs/{entry['id']}", headers=super_admin["headers"]
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["action"] == "company.approved"


async def test_login_audit_logs_carry_company_id(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver3@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    member = await register_and_login(client, email_service, "member@example.com")

    logs_resp = await client.get(
        "/api/v1/audit/logs",
        params={"action": "auth.login.success", "actor_user_id": member["user_id"]},
        headers=super_admin["headers"],
    )
    assert logs_resp.status_code == 200
    body = logs_resp.json()
    assert body["total"] >= 1
    assert body["items"][0]["company_id"] == member["company_id"]
    assert body["items"][0]["company_name"] == "Test Co"


async def test_get_audit_log_not_found(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver2@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    resp = await client.get(
        "/api/v1/audit/logs/00000000-0000-0000-0000-000000000000",
        headers=super_admin["headers"],
    )
    assert resp.status_code == 404
