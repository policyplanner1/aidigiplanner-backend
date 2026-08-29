from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def test_new_company_starts_at_registered_step(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin1@example.com")

    resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/onboarding", headers=admin["headers"]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # register_and_login already verifies the email, so onboarding has
    # advanced one step past "registered".
    assert body["onboarding_step"] == "email_verified"
    assert body["brand_structure"] is None
    assert body["products_count"] == 0


async def test_select_brand_structure_advances_onboarding_step(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin2@example.com")

    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/brand-structure",
        json={"brand_structure": "single_brand"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["brand_structure"] == "single_brand"
    assert resp.json()["onboarding_step"] == "brand_structure_selected"


async def test_single_brand_details_sets_industry_on_company_brand_profile(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin3@example.com")

    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/single-brand-details",
        json={"industry": "Insurance"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text

    profile = await client.get(
        f"/api/v1/companies/{admin['company_id']}/brand-profile", headers=admin["headers"]
    )
    assert profile.status_code == 200
    assert profile.json()["category"] == "Insurance"


async def test_confirming_company_brand_profile_advances_onboarding_step(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin4@example.com")

    put_resp = await client.put(
        f"/api/v1/companies/{admin['company_id']}/brand-profile",
        json={
            "name": "MPS Group",
            "category": "Business Services",
            "market": "India",
            "audience_primary": "Businesses",
            "compliance_mandatory_disclaimer": "",
        },
        headers=admin["headers"],
    )
    assert put_resp.status_code == 200, put_resp.text

    status_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/onboarding", headers=admin["headers"]
    )
    assert status_resp.json()["onboarding_step"] == "brand_profile_completed"


async def test_creating_first_product_advances_onboarding_step(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin5@example.com")

    create_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": "Policy Planner"},
        headers=admin["headers"],
    )
    assert create_resp.status_code == 201

    status_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/onboarding", headers=admin["headers"]
    )
    body = status_resp.json()
    assert body["onboarding_step"] == "first_product_created"
    assert body["products_count"] == 1


async def test_group_profile_and_complete_onboarding(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin6@example.com")

    group_resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/group-profile",
        json={"group_website_url": "https://mpsglobal.example.com"},
        headers=admin["headers"],
    )
    assert group_resp.status_code == 200, group_resp.text
    assert group_resp.json()["group_website_url"] == "https://mpsglobal.example.com"

    complete_resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/onboarding/complete", headers=admin["headers"]
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["onboarding_step"] == "completed"


async def test_onboarding_step_never_regresses(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin7@example.com")

    await client.post(
        f"/api/v1/companies/{admin['company_id']}/onboarding/complete", headers=admin["headers"]
    )
    # Re-selecting brand structure after onboarding is already "completed"
    # must not move the step backwards.
    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/brand-structure",
        json={"brand_structure": "multi_brand"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["onboarding_step"] == "completed"


async def test_only_company_admin_can_manage_onboarding(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "onboard-admin8@example.com")
    member = await register_and_login(client, email_service, "onboard-member8@example.com")
    await client.post(
        f"/api/v1/companies/{admin['company_id']}/members",
        json={"email": member["email"], "role": "member"},
        headers=admin["headers"],
    )

    resp = await client.patch(
        f"/api/v1/companies/{admin['company_id']}/brand-structure",
        json={"brand_structure": "single_brand"},
        headers=member["headers"],
    )
    assert resp.status_code == 403

    read_resp = await client.get(
        f"/api/v1/companies/{admin['company_id']}/onboarding", headers=member["headers"]
    )
    assert read_resp.status_code == 200
