from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Social Product") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


async def test_company_admin_can_add_and_list_social_account(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "social-admin1@example.com")
    product_id = await _create_product(client, admin)

    add_resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "instagram", "handle": "@brand", "profile_url": None},
        headers=admin["headers"],
    )
    assert add_resp.status_code == 201
    assert add_resp.json()["platform"] == "instagram"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


async def test_plain_product_member_can_add_social_account(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "social-admin2@example.com")
    viewer = await register_and_login(client, email_service, "social-viewer2@example.com")
    product_id = await _create_product(client, admin)

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

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "youtube", "handle": "BrandChannel"},
        headers=viewer["headers"],
    )
    assert resp.status_code == 201


async def test_outsider_gets_404_on_social_accounts(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "social-admin3@example.com")
    outsider = await register_and_login(client, email_service, "social-outsider3@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=outsider["headers"]
    )
    assert resp.status_code == 404


async def test_delete_requires_product_manager_role(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "social-admin4@example.com")
    viewer = await register_and_login(client, email_service, "social-viewer4@example.com")
    product_id = await _create_product(client, admin)

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

    add_resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "facebook", "handle": "brand.page"},
        headers=admin["headers"],
    )
    account_id = add_resp.json()["id"]

    forbidden = await client.delete(
        f"/api/v1/products/{product_id}/social-accounts/{account_id}", headers=viewer["headers"]
    )
    assert forbidden.status_code == 403

    ok = await client.delete(
        f"/api/v1/products/{product_id}/social-accounts/{account_id}", headers=admin["headers"]
    )
    assert ok.status_code == 204


async def test_duplicate_social_account_conflicts(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "social-admin5@example.com")
    product_id = await _create_product(client, admin)

    payload = {"platform": "twitter", "handle": "@brand"}
    first = await client.post(
        f"/api/v1/products/{product_id}/social-accounts", json=payload, headers=admin["headers"]
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/products/{product_id}/social-accounts", json=payload, headers=admin["headers"]
    )
    assert second.status_code == 409
