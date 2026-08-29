from typing import Any

from httpx import AsyncClient

from app.modules.creatives.pricing import get_creative_settings
from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Gen Product") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


def _brand_payload(*, banned_claims: list[str] | None = None) -> dict[str, Any]:
    return {
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
        "compliance_banned_claims": banned_claims or [],
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


async def _set_brand_profile(
    client: AsyncClient, admin: dict, product_id: str, *, banned_claims: list[str] | None = None
) -> None:
    resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(banned_claims=banned_claims),
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text


def _generate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_line": "travel",
        "topic": "Monsoon travel safety tips",
        "format": "post",
        "concept_count": 3,
    }
    payload.update(overrides)
    return payload


async def test_generate_persists_concepts_matching_concept_count(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "gen-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=3),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202, generate_resp.text
    job = generate_resp.json()
    assert job["status"] == "succeeded"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    assert list_resp.status_code == 200
    concepts = list_resp.json()
    assert len(concepts) == 3
    assert {c["concept_index"] for c in concepts} == {0, 1, 2}
    assert all(c["status"] == "draft" for c in concepts)
    assert all(c["compliance_rejected"] is False for c in concepts)
    assert all(c["job_id"] == job["id"] for c in concepts)


async def test_approve_and_reject_concept(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "gen-admin2@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=2),
        headers=admin["headers"],
    )
    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert generate_resp.status_code == 202

    approve_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concepts[0]['id']}/approve",
        headers=admin["headers"],
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"
    assert approve_resp.json()["reviewed_by"] is not None

    reject_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concepts[1]['id']}/reject",
        json={"reason": "Off-brand tone"},
        headers=admin["headers"],
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    detail_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives/concepts/{concepts[0]['id']}",
        headers=admin["headers"],
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "approved"


async def test_compliance_gate_rejects_and_reports_never_silently_drops(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "gen-admin3@example.com")
    product_id = await _create_product(client, admin)
    # "travel insurance" is guaranteed to appear in the mock provider's own
    # generated caption ("here's what travel insurance actually covers...")
    # since the product label is "Travel Insurance" -- banning that phrase
    # deterministically trips check_banned_claims for every concept, on
    # both the original attempt and the compliance gate's one retry.
    await _set_brand_profile(client, admin, product_id, banned_claims=["travel insurance"])

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=2),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202
    # The job itself still succeeds (technical completion) even though
    # every concept fails compliance -- that's a valid content outcome,
    # not a pipeline failure.
    assert generate_resp.json()["status"] == "succeeded"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 2
    assert all(c["compliance_rejected"] is True for c in concepts)
    assert all(c["compliance_rejection_reason"] for c in concepts)


async def test_generate_is_idempotent_unless_forced(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "gen-admin4@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    first = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    second = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert first.json()["id"] == second.json()["id"]
    assert len(arq_pool.enqueued) == 1

    forced = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(force=True),
        headers=admin["headers"],
    )
    assert forced.json()["id"] != first.json()["id"]
    assert len(arq_pool.enqueued) == 2


async def test_unknown_product_line_400s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "gen-admin5@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(product_line="motor"),
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert arq_pool.enqueued == []


async def test_generate_over_per_run_cost_cap_400s(
    client: AsyncClient,
    email_service: RecordingEmailService,
    arq_pool: FakeArqPool,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CREATIVE_MAX_COST_PER_RUN_INR", "0.01")
    get_creative_settings.cache_clear()
    try:
        admin = await register_and_login(client, email_service, "gen-admin6@example.com")
        product_id = await _create_product(client, admin)
        await _set_brand_profile(client, admin, product_id)

        resp = await client.post(
            f"/api/v1/products/{product_id}/creatives/generate",
            json=_generate_payload(),
            headers=admin["headers"],
        )
        assert resp.status_code == 400
        assert "per-run cap" in resp.json()["error"]["message"]
        assert arq_pool.enqueued == []
    finally:
        get_creative_settings.cache_clear()
