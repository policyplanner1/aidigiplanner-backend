from httpx import AsyncClient

from tests.factories import login, register_and_login
from tests.fakes import RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Policy Planner") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


async def test_invite_new_user_creates_company_and_product_membership(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "invite-admin1@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/invitations",
        json={
            "email": "content@example.com",
            "full_name": "Content Creator",
            "role": "creator",
        },
        headers=admin["headers"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user_email"] == "content@example.com"
    assert body["role"] == "creator"

    temporary_password = email_service.new_member_credentials["content@example.com"]
    tokens = await login(client, "content@example.com", temporary_password)
    assert tokens["access_token"]

    company_members = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=admin["headers"]
    )
    assert any(m["user_email"] == "content@example.com" for m in company_members.json())


async def test_invite_existing_company_member_only_adds_product_membership(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "invite-admin2@example.com")
    member = await register_and_login(client, email_service, "invite-member2@example.com")
    product_id = await _create_product(client, admin)

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )

    resp = await client.post(
        f"/api/v1/products/{product_id}/invitations",
        json={"email": member["email"], "role": "approver"},
        headers=admin["headers"],
    )
    assert resp.status_code == 201, resp.text
    assert member["email"] not in email_service.new_member_credentials


async def test_invite_existing_user_with_no_company_membership_gets_added_to_company_too(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "invite-admin3@example.com")
    outsider = await register_and_login(
        client, email_service, "invite-outsider3@example.com", company_name="Elsewhere Co"
    )
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/invitations",
        json={"email": outsider["email"], "role": "creator"},
        headers=admin["headers"],
    )
    assert resp.status_code == 201, resp.text
    assert outsider["email"] not in email_service.new_member_credentials

    company_members = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=admin["headers"]
    )
    assert any(m["user_email"] == outsider["email"] for m in company_members.json())


async def test_invite_duplicate_product_member_conflicts(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "invite-admin4@example.com")
    product_id = await _create_product(client, admin)

    payload = {"email": "content4@example.com", "full_name": "Content", "role": "creator"}
    first = await client.post(
        f"/api/v1/products/{product_id}/invitations", json=payload, headers=admin["headers"]
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/products/{product_id}/invitations", json=payload, headers=admin["headers"]
    )
    assert second.status_code == 409
