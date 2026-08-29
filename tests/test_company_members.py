from httpx import AsyncClient

from tests.factories import login, register_and_login
from tests.fakes import RecordingEmailService


async def test_add_new_user_by_email_sends_credentials_and_they_can_login(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "member-admin1@example.com")

    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": "newbie@example.com", "full_name": "New Bie", "role": "member"},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_email"] == "newbie@example.com"

    temporary_password = email_service.new_member_credentials["newbie@example.com"]
    tokens = await login(client, "newbie@example.com", temporary_password)
    assert tokens["access_token"]


async def test_add_existing_user_by_email_links_without_sending_credentials(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "member-admin2@example.com")
    existing = await register_and_login(
        client, email_service, "existing-elsewhere@example.com", company_name="Elsewhere Co"
    )

    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": existing["email"], "role": "member"},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    assert existing["email"] not in email_service.new_member_credentials

    me_resp = await client.get("/api/v1/auth/me", headers=existing["headers"])
    company_ids = {c["company_id"] for c in me_resp.json()["companies"]}
    assert admin["company_id"] in company_ids
    assert existing["company_id"] in company_ids  # still a member of their own company too


async def test_add_new_user_without_full_name_rejected(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "member-admin3@example.com")

    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": "no-name@example.com", "role": "member"},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


async def test_add_same_existing_member_twice_conflicts(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "member-admin4@example.com")
    other = await register_and_login(client, email_service, "member-dup@example.com")

    first = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": other["email"], "role": "member"},
        headers=admin["headers"],
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": other["email"], "role": "member"},
        headers=admin["headers"],
    )
    assert second.status_code == 409


async def test_list_members_shows_assigned_product_names_or_null(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "member-admin5@example.com")
    member = await register_and_login(client, email_service, "member-proj5@example.com")

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )
    product_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Assigned Product"},
        headers=admin["headers"],
    )
    product_id = product_resp.json()["id"]
    await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": member["user_id"], "role": "analyst"},
        headers=admin["headers"],
    )

    list_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/members", headers=admin["headers"]
    )
    assert list_resp.status_code == 200
    rows = {row["user_id"]: row for row in list_resp.json()}
    assert rows[member["user_id"]]["products"] == ["Assigned Product"]
    # Company admins have no explicit ProductMember row (they bypass the
    # check entirely), so this reflects explicit assignments only, not
    # everything they can actually access.
    assert rows[admin["user_id"]]["products"] is None
