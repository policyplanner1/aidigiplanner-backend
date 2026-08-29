from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.factories import login, register_and_login
from tests.fakes import RecordingEmailService


async def _promote_to_super_admin(db_session: AsyncSession, user_id: str) -> None:
    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()


async def test_non_super_admin_forbidden_on_suspend_and_delete(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "plain-admin@example.com")

    suspend_resp = await client.post(
        f"/api/v1/admin/companies/{admin['company_id']}/suspend",
        json={"reason": "Suspected abuse."},
        headers=admin["headers"],
    )
    assert suspend_resp.status_code == 403

    delete_resp = await client.delete(
        f"/api/v1/admin/companies/{admin['company_id']}", headers=admin["headers"]
    )
    assert delete_resp.status_code == 403


async def test_super_admin_suspend_blocks_login_and_can_conflict(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver1@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    admin = await register_and_login(client, email_service, "suspended-admin@example.com")

    suspend_resp = await client.post(
        f"/api/v1/admin/companies/{admin['company_id']}/suspend",
        json={"reason": "Suspected abuse."},
        headers=super_admin["headers"],
    )
    assert suspend_resp.status_code == 200
    assert suspend_resp.json()["status"] == "suspended"
    assert email_service.suspended_companies["suspended-admin@example.com"] == (
        "Test Co",
        "Suspected abuse.",
    )

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "suspended-admin@example.com", "password": "correct-horse-battery"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "company_suspended"

    already_suspended_resp = await client.post(
        f"/api/v1/admin/companies/{admin['company_id']}/suspend",
        json={"reason": "Suspected abuse again."},
        headers=super_admin["headers"],
    )
    assert already_suspended_resp.status_code == 409


async def test_super_admin_delete_blocks_login_and_hides_company(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver2@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    admin = await register_and_login(client, email_service, "deleted-admin@example.com")

    # Confirm the pre-delete flow works, so the post-delete assertions below
    # are a real regression check rather than a null result.
    working_login = await login(client, "deleted-admin@example.com")
    assert working_login["access_token"]

    delete_resp = await client.delete(
        f"/api/v1/admin/companies/{admin['company_id']}", headers=super_admin["headers"]
    )
    assert delete_resp.status_code == 204
    assert email_service.deleted_companies["deleted-admin@example.com"] == "Test Co"

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "deleted-admin@example.com", "password": "correct-horse-battery"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "company_deleted"

    detail_resp = await client.get(
        f"/api/v1/admin/companies/{admin['company_id']}", headers=super_admin["headers"]
    )
    assert detail_resp.status_code == 404

    list_resp = await client.get("/api/v1/admin/companies", headers=super_admin["headers"])
    assert list_resp.status_code == 200
    assert all(c["id"] != admin["company_id"] for c in list_resp.json())

    already_deleted_resp = await client.delete(
        f"/api/v1/admin/companies/{admin['company_id']}", headers=super_admin["headers"]
    )
    assert already_deleted_resp.status_code == 404
