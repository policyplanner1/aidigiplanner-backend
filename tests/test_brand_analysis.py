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


async def test_analyze_company_brand_profile_uses_mock_without_gemini_key(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "analysis-admin1@example.com")

    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/brand-profile/analyze",
        json={"description": "MPS Group provides professional business services.", "dry_run": True},
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "company"
    assert body["owner_id"] == admin["company_id"]
    assert body["name"]
    assert body["description"]

    get_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/brand-profile", headers=admin["headers"]
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == body["id"]


async def test_analyze_requires_website_or_description(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "analysis-admin2@example.com")

    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/brand-profile/analyze",
        json={},
        headers=admin["headers"],
    )
    assert resp.status_code == 422


async def test_analyze_product_brand_profile_then_effective_matches(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "analysis-admin3@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/brand-profile/analyze",
        json={
            "description": "Policy Planner helps families find the right insurance.",
            "dry_run": True,
        },
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "product"

    effective = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/effective", headers=admin["headers"]
    )
    assert effective.status_code == 200
    assert effective.json()["id"] == resp.json()["id"]


async def test_product_effective_falls_back_to_company_brand_when_use_company_branding(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "analysis-admin4@example.com")
    product_id = await _create_product(client, admin)

    # No product-level profile yet -- resolving effective 400s.
    no_profile_resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/effective", headers=admin["headers"]
    )
    assert no_profile_resp.status_code == 400

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/brand-profile/analyze",
        json={"description": "MPS Group is a business services company.", "dry_run": True},
        headers=admin["headers"],
    )
    switch_resp = await client.patch(
        f"/api/v1/products/{product_id}",
        json={"branding_mode": "use_company_branding"},
        headers=admin["headers"],
    )
    assert switch_resp.status_code == 200, switch_resp.text

    effective = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/effective", headers=admin["headers"]
    )
    assert effective.status_code == 200
    assert effective.json()["scope"] == "company"
    assert effective.json()["owner_id"] == admin["company_id"]
