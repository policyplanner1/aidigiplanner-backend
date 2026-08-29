from typing import Any

from httpx import AsyncClient

from app.models.user import User
from app.modules.creatives.pricing import get_creative_settings
from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _promote_to_super_admin(db_session: Any, user_id: str) -> None:
    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_super_admin = True
    await db_session.commit()


async def _create_product(client: AsyncClient, admin: dict, name: str = "Hardening Product") -> str:
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
        "compliance_banned_claims": [],
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


def _generate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_line": "travel",
        "topic": "Monsoon travel safety tips",
        "format": "post",
        "concept_count": 1,
    }
    payload.update(overrides)
    return payload


async def test_per_day_cost_cap_trips_after_a_prior_job_today(
    client: AsyncClient,
    email_service: RecordingEmailService,
    arq_pool: FakeArqPool,
    monkeypatch: Any,
) -> None:
    admin = await register_and_login(client, email_service, "hardening-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    first = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert first.status_code == 202, first.text
    first_cost = first.json()["estimated_cost_inr"]
    assert first_cost > 0

    # Tighten the daily cap to just above one job's cost but below two --
    # the per-run cap is raised out of the way so only the per-day check
    # can be the thing that rejects the second call.
    monkeypatch.setenv("CREATIVE_MAX_COST_PER_RUN_INR", "999999")
    monkeypatch.setenv("CREATIVE_MAX_COST_PER_DAY_INR", str(first_cost * 1.5))
    get_creative_settings.cache_clear()
    try:
        second = await client.post(
            f"/api/v1/products/{product_id}/creatives/generate",
            json=_generate_payload(topic="A different topic to dodge idempotency", force=True),
            headers=admin["headers"],
        )
        assert second.status_code == 400, second.text
        assert "per-day cap" in second.json()["error"]["message"]
        # Only the first job was ever enqueued -- the second never got that
        # far, so the arq pool never got a second call.
        assert len(arq_pool.enqueued) == 1
    finally:
        get_creative_settings.cache_clear()


async def test_audit_log_records_job_and_concept_lifecycle(
    client: AsyncClient,
    email_service: RecordingEmailService,
    arq_pool: FakeArqPool,
    db_session: Any,
) -> None:
    admin = await register_and_login(client, email_service, "hardening-admin2@example.com")
    await _promote_to_super_admin(db_session, admin["user_id"])
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]

    queued_logs = await client.get(
        "/api/v1/audit/logs",
        params={"action": "creative_job.queued", "product_id": product_id},
        headers=admin["headers"],
    )
    assert queued_logs.json()["total"] == 1
    assert queued_logs.json()["items"][0]["resource_type"] == "generation_job"

    succeeded_logs = await client.get(
        "/api/v1/audit/logs",
        params={"action": "creative_job.succeeded", "product_id": product_id},
        headers=admin["headers"],
    )
    assert succeeded_logs.json()["total"] == 1
    assert succeeded_logs.json()["items"][0]["resource_id"] == job_id

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concept_id = list_resp.json()[0]["id"]
    await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
        headers=admin["headers"],
    )

    approved_logs = await client.get(
        "/api/v1/audit/logs",
        params={"action": "creative_concept.approved", "product_id": product_id},
        headers=admin["headers"],
    )
    assert approved_logs.json()["total"] == 1
    assert approved_logs.json()["items"][0]["resource_id"] == concept_id


async def test_asset_rendering_failure_yields_partially_failed_but_keeps_concepts(
    client: AsyncClient,
    email_service: RecordingEmailService,
    arq_pool: FakeArqPool,
    db_session: Any,
    monkeypatch: Any,
) -> None:
    admin = await register_and_login(client, email_service, "hardening-admin3@example.com")
    await _promote_to_super_admin(db_session, admin["user_id"])
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated image provider outage")

    monkeypatch.setattr("app.modules.creatives.worker.render_images_for_concepts", _boom)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202, generate_resp.text
    body = generate_resp.json()
    assert body["status"] == "partially_failed"
    assert body["error_message"] is not None
    assert "simulated image provider outage" in body["error_message"]

    # The ideation work (concepts) survives the asset-rendering failure --
    # only the (nonexistent) assets are missing.
    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 1
    assert concepts[0]["assets"] == []

    partial_logs = await client.get(
        "/api/v1/audit/logs",
        params={"action": "creative_job.partially_failed", "product_id": product_id},
        headers=admin["headers"],
    )
    assert partial_logs.json()["total"] == 1
