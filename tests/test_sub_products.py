from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Policy Planner") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


async def _add_product_member(
    client: AsyncClient, admin: dict, product_id: str, member: dict, role: str
) -> None:
    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )
    resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": member["user_id"], "role": role},
        headers=admin["headers"],
    )
    assert resp.status_code == 201


async def test_create_sub_products_batch(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin1@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/sub-products",
        json={"names": ["Health Insurance", "Term Insurance", "Motor Insurance"]},
        headers=admin["headers"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [sp["name"] for sp in body] == ["Health Insurance", "Term Insurance", "Motor Insurance"]
    assert all(sp["branding_mode"] == "use_product_branding" for sp in body)
    assert all(sp["status"] == "active" for sp in body)

    listed = await client.get(
        f"/api/v1/products/{product_id}/sub-products", headers=admin["headers"]
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 3


async def test_sub_product_slug_collision_gets_suffixed(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin2@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/sub-products",
        json={"names": ["Health Insurance", "Health Insurance"]},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    slugs = [sp["slug"] for sp in resp.json()]
    assert slugs == ["health-insurance", "health-insurance-2"]


async def test_update_sub_product_name_status_and_branding_mode(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin3@example.com")
    product_id = await _create_product(client, admin)
    created = (
        await client.post(
            f"/api/v1/products/{product_id}/sub-products",
            json={"names": ["Health Insurance"]},
            headers=admin["headers"],
        )
    ).json()
    sub_product_id = created[0]["id"]

    resp = await client.patch(
        f"/api/v1/sub-products/{sub_product_id}",
        json={"name": "Health Cover", "status": "archived", "branding_mode": "separate_brand"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Health Cover"
    assert body["slug"] == "health-cover"
    assert body["status"] == "archived"
    assert body["branding_mode"] == "separate_brand"


async def test_delete_sub_product_removes_it_from_listing(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin4@example.com")
    product_id = await _create_product(client, admin)
    created = (
        await client.post(
            f"/api/v1/products/{product_id}/sub-products",
            json={"names": ["Health Insurance"]},
            headers=admin["headers"],
        )
    ).json()
    sub_product_id = created[0]["id"]

    delete_resp = await client.delete(
        f"/api/v1/sub-products/{sub_product_id}", headers=admin["headers"]
    )
    assert delete_resp.status_code == 204

    listed = await client.get(
        f"/api/v1/products/{product_id}/sub-products", headers=admin["headers"]
    )
    assert listed.json() == []


async def test_creator_cannot_create_or_delete_sub_products_but_can_list(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin5@example.com")
    creator = await register_and_login(client, email_service, "subproduct-editor5@example.com")
    product_id = await _create_product(client, admin)
    await _add_product_member(client, admin, product_id, creator, "creator")

    create_resp = await client.post(
        f"/api/v1/products/{product_id}/sub-products",
        json={"names": ["Health Insurance"]},
        headers=creator["headers"],
    )
    assert create_resp.status_code == 403

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/sub-products", headers=creator["headers"]
    )
    assert list_resp.status_code == 200


async def test_outsider_gets_404_on_sub_products(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin6@example.com")
    outsider = await register_and_login(client, email_service, "subproduct-outsider6@example.com")
    product_id = await _create_product(client, admin)
    created = (
        await client.post(
            f"/api/v1/products/{product_id}/sub-products",
            json={"names": ["Health Insurance"]},
            headers=admin["headers"],
        )
    ).json()
    sub_product_id = created[0]["id"]

    resp = await client.get(
        f"/api/v1/products/{product_id}/sub-products", headers=outsider["headers"]
    )
    assert resp.status_code == 404

    resp2 = await client.patch(
        f"/api/v1/sub-products/{sub_product_id}",
        json={"name": "Hack"},
        headers=outsider["headers"],
    )
    assert resp2.status_code == 404


async def test_product_member_scoped_to_other_sub_products_gets_404(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "subproduct-admin7@example.com")
    member = await register_and_login(client, email_service, "subproduct-member7@example.com")
    product_id = await _create_product(client, admin)
    created = (
        await client.post(
            f"/api/v1/products/{product_id}/sub-products",
            json={"names": ["Health Insurance", "Motor Insurance"]},
            headers=admin["headers"],
        )
    ).json()
    health_id, motor_id = created[0]["id"], created[1]["id"]

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )
    add_member_resp = await client.post(
        f"/api/v1/products/{product_id}/members",
        json={"user_id": member["user_id"], "role": "creator", "sub_product_ids": [health_id]},
        headers=admin["headers"],
    )
    assert add_member_resp.status_code == 201

    # Give "health" a brand profile so a granted-access read returns 200,
    # distinguishing it from a scope-denied 404 on "motor" below.
    put_resp = await client.put(
        f"/api/v1/sub-products/{health_id}/brand-profile",
        json={
            "name": "Health Cover",
            "category": "Insurance",
            "market": "India",
            "audience_primary": "Families",
            "compliance_mandatory_disclaimer": "T&C apply.",
        },
        headers=admin["headers"],
    )
    assert put_resp.status_code == 200, put_resp.text

    allowed = await client.get(
        f"/api/v1/sub-products/{health_id}/brand-profile", headers=member["headers"]
    )
    assert allowed.status_code == 200

    blocked = await client.get(
        f"/api/v1/sub-products/{motor_id}/brand-profile", headers=member["headers"]
    )
    assert blocked.status_code == 404
