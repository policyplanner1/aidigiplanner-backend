from typing import Any

from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Creatives Product") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


async def _set_brand_profile(client: AsyncClient, admin: dict, product_id: str) -> None:
    payload = {
        "name": "TestBrand",
        "category": "Insurance",
        "market": "India",
        "audience_primary": "Young professionals",
        "audience_secondary": "",
        "tone": ["confident"],
        "languages": ["en"],
        "visual_identity": {
            "palette": ["#0057B8"],
            "heading_font": "Inter",
            "body_font": "Inter",
            "style_keywords": ["clean"],
            "avoid": ["clutter"],
        },
        "compliance_mandatory_disclaimer": "Terms and conditions apply.",
        "compliance_secondary_disclaimers": [],
        "compliance_banned_claims": ["guaranteed returns"],
        "compliance_rules": ["Always show the disclaimer."],
        "cta_bank": ["Get a quote", "Learn more"],
        "hashtag_bank": ["#insurance", "#travel"],
        "product_lines": [
            {
                "id": "travel",
                "label": "Travel Insurance",
                "partners": [],
                "hooks": ["flight cancelled", "lost luggage"],
            }
        ],
    }
    resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile", json=payload, headers=admin["headers"]
    )
    assert resp.status_code == 200, resp.text


def _generate_payload() -> dict[str, Any]:
    return {
        "product_line": "travel",
        "topic": "Monsoon travel safety tips",
        "format": "post",
    }


async def test_generate_creatives_enqueues_and_job_reaches_terminal_status(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "creatives-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202, generate_resp.text
    job = generate_resp.json()
    assert job["product_id"] == product_id
    assert job["dry_run"] is False
    assert job["estimated_cost_inr"] > 0

    # FakeArqPool runs the worker task inline during enqueue_job(), so by
    # the time the request returns the pipeline has already run through to
    # a terminal status (mock provider, since no GEMINI_API_KEY is set).
    assert job["status"] == "succeeded"
    assert len(arq_pool.enqueued) == 1
    assert arq_pool.enqueued[0][0] == "generate_creatives_job"

    poll_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives/jobs/{job['id']}", headers=admin["headers"]
    )
    assert poll_resp.status_code == 200
    assert poll_resp.json()["status"] == "succeeded"
    assert poll_resp.json()["started_at"] is not None
    assert poll_resp.json()["finished_at"] is not None


async def test_generate_without_brand_profile_400s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "creatives-admin-nobrand@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert arq_pool.enqueued == []


async def test_viewer_cannot_generate_but_can_poll(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "creatives-admin2@example.com")
    viewer = await register_and_login(client, email_service, "creatives-viewer2@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

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

    forbidden = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=viewer["headers"],
    )
    assert forbidden.status_code == 403

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]

    poll_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}", headers=viewer["headers"]
    )
    assert poll_resp.status_code == 200


async def test_generate_creatives_for_missing_product_404s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "creatives-admin3@example.com")

    resp = await client.post(
        "/api/v1/products/00000000-0000-0000-0000-000000000000/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert resp.status_code == 404
    assert arq_pool.enqueued == []


async def test_poll_job_from_wrong_product_404s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "creatives-admin4@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    other_product_id = await _create_product(client, admin, name="Other Product")

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/products/{other_product_id}/creatives/jobs/{job_id}", headers=admin["headers"]
    )
    assert resp.status_code == 404
