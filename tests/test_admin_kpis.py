from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.factories import register_and_login, register_user, verify_user
from tests.fakes import RecordingEmailService


async def _promote_to_super_admin(db_session: AsyncSession, user_id: str) -> None:
    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()


async def test_non_super_admin_forbidden_on_kpis_endpoint(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "plain-admin@example.com")

    resp = await client.get("/api/v1/admin/kpis", headers=admin["headers"])
    assert resp.status_code == 403


async def test_kpis_reflect_company_user_and_social_account_state(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    # Company A: registered but never verified -> user pending, company pending_approval.
    await register_user(client, "owner-a@example.com", company_name="Company A")

    # Company B: verified and approved by the super admin -> user active, company active.
    reg_b = await register_user(client, "owner-b@example.com", company_name="Company B")
    await verify_user(client, email_service, "owner-b@example.com")
    approve_resp = await client.post(
        f"/api/v1/admin/companies/{reg_b['company']['id']}/approve",
        headers=super_admin["headers"],
    )
    assert approve_resp.status_code == 200
    login_b = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner-b@example.com", "password": "correct-horse-battery"},
    )
    assert login_b.status_code == 200
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    # Company C: verified then rejected by the super admin -> user active, company rejected.
    reg_c = await register_user(client, "owner-c@example.com", company_name="Company C")
    await verify_user(client, email_service, "owner-c@example.com")
    reject_resp = await client.post(
        f"/api/v1/admin/companies/{reg_c['company']['id']}/reject",
        json={"reason": "Incomplete details."},
        headers=super_admin["headers"],
    )
    assert reject_resp.status_code == 200

    # Two social accounts under Company B's product; remove one to prove
    # soft-deleted accounts drop out of the KPI counts.
    product_resp = await client.post(
        f"/api/v1/companies/{reg_b['company']['id']}/products",
        json={"name": "Campaign"},
        headers=headers_b,
    )
    assert product_resp.status_code == 201
    product_id = product_resp.json()["id"]

    ig_resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "instagram", "handle": "@companyb"},
        headers=headers_b,
    )
    assert ig_resp.status_code == 201

    fb_resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "facebook", "handle": "companyb"},
        headers=headers_b,
    )
    assert fb_resp.status_code == 201
    fb_account_id = fb_resp.json()["id"]

    remove_resp = await client.delete(
        f"/api/v1/products/{product_id}/social-accounts/{fb_account_id}",
        headers=headers_b,
    )
    assert remove_resp.status_code == 204

    kpi_resp = await client.get("/api/v1/admin/kpis", headers=super_admin["headers"])
    assert kpi_resp.status_code == 200
    body = kpi_resp.json()

    # 4 companies total: super admin's own (auto-approved active) + A + B + C.
    assert body["companies"] == {
        "total": 4,
        "pending_approval": 1,
        "active": 2,
        "rejected": 1,
        "suspended": 0,
    }

    # 4 users total: super admin + the three owners. Owner A never verified.
    assert body["users"]["total"] == 4
    assert body["users"]["pending"] == 1
    assert body["users"]["active"] == 3
    assert body["users"]["suspended"] == 0
    assert body["users"]["super_admins"] == 1

    # Only the surviving Instagram account counts; the removed Facebook one doesn't.
    assert body["social_accounts"]["total"] == 1
    assert body["social_accounts"]["active"] == 1
    assert body["social_accounts"]["disabled"] == 0
    assert body["social_accounts"]["by_platform"]["instagram"] == 1
    assert body["social_accounts"]["by_platform"]["facebook"] == 0
