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


async def test_non_super_admin_forbidden_on_admin_users_endpoints(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "plain-admin@example.com")

    resp = await client.get("/api/v1/admin/users", headers=admin["headers"])
    assert resp.status_code == 403


async def test_super_admin_lists_and_filters_users(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    admin = await register_and_login(client, email_service, "company-admin@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Launch Campaign"},
        headers=admin["headers"],
    )
    assert product_resp.status_code == 201
    product_id = product_resp.json()["id"]

    # A company_admin gets implicit full product access without an explicit
    # ProductMember row, so add one explicitly to exercise the "products"
    # field on the list response (which reflects real membership rows).
    member_resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": admin["user_id"], "role": "product_manager"},
        headers=admin["headers"],
    )
    assert member_resp.status_code == 201

    list_resp = await client.get("/api/v1/admin/users", headers=super_admin["headers"])
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] >= 2
    assert body["limit"] == 50
    assert body["offset"] == 0

    entry = next(item for item in body["items"] if item["id"] == admin["user_id"])
    assert entry["email"] == "company-admin@example.com"
    assert any(c["company_id"] == admin["company_id"] for c in entry["companies"])
    assert entry["companies"][0]["role"] == "company_admin"
    assert any(
        p["product_id"] == product_id and p["product_name"] == "Launch Campaign"
        for p in entry["products"]
    )

    filtered_resp = await client.get(
        "/api/v1/admin/users",
        params={"company_id": admin["company_id"]},
        headers=super_admin["headers"],
    )
    assert filtered_resp.status_code == 200
    filtered_body = filtered_resp.json()
    assert all(
        any(c["company_id"] == admin["company_id"] for c in item["companies"])
        for item in filtered_body["items"]
    )
    assert any(item["id"] == admin["user_id"] for item in filtered_body["items"])


async def test_super_admin_gets_full_user_detail(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver2@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    admin = await register_and_login(client, email_service, "owner@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Launch Campaign"},
        headers=admin["headers"],
    )
    assert product_resp.status_code == 201
    product_id = product_resp.json()["id"]

    # A company_admin gets implicit full product access without an explicit
    # ProductMember row, so add one explicitly to exercise the "products"
    # field on the user-detail response (which reflects real membership rows).
    member_resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": admin["user_id"], "role": "product_manager"},
        headers=admin["headers"],
    )
    assert member_resp.status_code == 201

    social_resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "instagram", "handle": "@launch"},
        headers=admin["headers"],
    )
    assert social_resp.status_code == 201

    detail_resp = await client.get(
        f"/api/v1/admin/users/{admin['user_id']}", headers=super_admin["headers"]
    )
    assert detail_resp.status_code == 200
    body = detail_resp.json()
    assert body["email"] == "owner@example.com"
    assert any(c["company_id"] == admin["company_id"] for c in body["companies"])
    assert any(p["product_id"] == product_id for p in body["products"])
    assert any(s["handle"] == "@launch" for s in body["social_accounts"])


async def test_get_user_not_found(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    super_admin = await register_and_login(client, email_service, "approver3@example.com")
    await _promote_to_super_admin(db_session, super_admin["user_id"])

    resp = await client.get(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000",
        headers=super_admin["headers"],
    )
    assert resp.status_code == 404
