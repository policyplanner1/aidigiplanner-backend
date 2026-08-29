from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def test_outsider_gets_404_not_403_on_company_members(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin1@example.com")
    outsider = await register_and_login(client, email_service, "rbac-outsider1@example.com")

    resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=outsider["headers"]
    )
    assert resp.status_code == 404


async def test_plain_member_gets_403_on_admin_only_action(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin2@example.com")
    other = await register_and_login(client, email_service, "rbac-member2@example.com")

    # admin directly adds `other` as a plain member of their company.
    add_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": other["email"], "role": "member"},
        headers=admin["headers"],
    )
    assert add_resp.status_code == 201

    # `other` is now a real member of admin's company, but not an admin —
    # admin-only actions must 403, not 404 (they legitimately know it exists).
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": admin["email"], "role": "member"},
        headers=other["headers"],
    )
    assert resp.status_code == 403


async def test_nonexistent_company_returns_404(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin3@example.com")
    resp = await client.get(
        "/api/v1/companies/00000000-0000-7000-8000-000000000000/members",
        headers=admin["headers"],
    )
    assert resp.status_code == 404


async def test_company_admin_bypasses_product_role_check(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin4@example.com")
    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Bypass Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]

    # Admin has no explicit product_members row, yet can manage the product.
    resp = await client.get(f"/api/v1/products/{product_id}/members", headers=admin["headers"])
    assert resp.status_code == 200


async def test_product_member_wrong_role_gets_403(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin5@example.com")
    viewer = await register_and_login(client, email_service, "rbac-viewer5@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Role Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": viewer["email"], "role": "member"},
        headers=admin["headers"],
    )
    await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": viewer["user_id"], "role": "analyst"},
        headers=admin["headers"],
    )

    # viewer role can't add other product members (product_manager-only action).
    resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": admin["user_id"], "role": "creator"},
        headers=viewer["headers"],
    )
    assert resp.status_code == 403


async def test_same_company_member_unassigned_to_product_gets_404(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin6@example.com")
    member = await register_and_login(client, email_service, "rbac-member6@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Unassigned Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )
    # member is a real company member but was never added to this product.
    resp = await client.get(f"/api/v1/products/{product_id}/members", headers=member["headers"])
    assert resp.status_code == 404


async def test_total_outsider_gets_404_on_product(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin7@example.com")
    outsider = await register_and_login(client, email_service, "rbac-outsider7@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Outsider Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]

    resp = await client.get(f"/api/v1/products/{product_id}/members", headers=outsider["headers"])
    assert resp.status_code == 404


async def test_cannot_demote_last_company_admin(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin8@example.com")
    members_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=admin["headers"]
    )
    admin_member_id = members_resp.json()[0]["id"]

    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/members/{admin_member_id}",
        json={"role": "member"},
        headers=admin["headers"],
    )
    assert resp.status_code == 409


async def test_cannot_remove_last_company_admin(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin9@example.com")
    members_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=admin["headers"]
    )
    admin_member_id = members_resp.json()[0]["id"]

    resp = await client.delete(
        f"/api/v1/companies/{admin['company_id']}/members/{admin_member_id}",
        headers=admin["headers"],
    )
    assert resp.status_code == 409


async def test_second_admin_can_be_demoted(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin10@example.com")
    second = await register_and_login(client, email_service, "rbac-second10@example.com")

    add_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": second["email"], "role": "company_admin"},
        headers=admin["headers"],
    )
    second_member_id = add_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/members/{second_member_id}",
        json={"role": "member"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200


async def test_add_product_member_requires_company_membership_first(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin11@example.com")
    stranger = await register_and_login(client, email_service, "rbac-stranger11@example.com")

    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Guarded Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": stranger["user_id"], "role": "analyst"},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


async def test_super_admin_bypasses_all_tenant_checks(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    admin = await register_and_login(client, email_service, "rbac-admin12@example.com")
    super_admin_ctx = await register_and_login(client, email_service, "rbac-super12@example.com")

    user = await db_session.get(User, super_admin_ctx["user_id"])
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()

    # Fresh login so token_version/claims reflect... (is_super_admin is
    # resolved from the DB on every request, not from the JWT, so the
    # existing token already works without a new login.)
    resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=super_admin_ctx["headers"]
    )
    assert resp.status_code == 200
