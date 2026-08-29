from typing import Any

from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Content Product") -> str:
    resp = await client.post(
        f"/api/v1/companies/{admin['company_id']}/products",
        json={"name": name},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    return resp.json()["id"]  # type: ignore[no-any-return]


def _brand_payload() -> dict[str, Any]:
    return {
        "name": "TestBrand",
        "category": "Insurance",
        "market": "India",
        "audience_primary": "Young professionals",
        "compliance_mandatory_disclaimer": "Terms and conditions apply.",
        "cta_bank": ["Get a quote"],
        "product_lines": [{"id": "travel", "label": "Travel Insurance"}],
    }


async def _set_brand_profile(client: AsyncClient, admin: dict, product_id: str) -> None:
    resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(),
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text


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


async def _generate_one_concept(
    client: AsyncClient, admin: dict, product_id: str
) -> str:
    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json={
            "product_line": "travel",
            "topic": "Why families need travel insurance",
            "format": "post",
            "concept_count": 1,
        },
        headers=admin["headers"],
    )
    assert resp.status_code == 202, resp.text
    concepts = (
        await client.get(f"/api/v1/products/{product_id}/creatives", headers=admin["headers"])
    ).json()
    return concepts[0]["id"]  # type: ignore[no-any-return]


async def test_new_concept_starts_as_draft(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    detail = await client.get(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}",
        headers=admin["headers"],
    )
    assert detail.json()["status"] == "draft"


async def test_submit_approve_schedule_publish_flow(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin2@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    submit = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/submit-for-review",
        headers=admin["headers"],
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "in_review"

    approve = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
        headers=admin["headers"],
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    schedule = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/schedule",
        json={"scheduled_at": "2026-09-01T10:00:00"},
        headers=admin["headers"],
    )
    assert schedule.status_code == 200, schedule.text
    assert schedule.json()["status"] == "scheduled"

    publish = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/publish",
        headers=admin["headers"],
    )
    assert publish.status_code == 200
    assert publish.json()["status"] == "published"
    assert publish.json()["published_at"] is not None


async def test_cannot_schedule_a_draft_concept(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin3@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/schedule",
        json={"scheduled_at": "2026-09-01T10:00:00"},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


async def test_request_changes_rejects_and_files_comment(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin4@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    reject = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/reject",
        json={"reason": "Tone is off-brand"},
        headers=admin["headers"],
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"

    comments = await client.get(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/comments",
        headers=admin["headers"],
    )
    assert comments.status_code == 200
    bodies = [c["body"] for c in comments.json()]
    assert "Tone is off-brand" in bodies


async def test_add_comment_directly(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin5@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/comments",
        json={"body": "Can we brighten the image?"},
        headers=admin["headers"],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"] == "Can we brighten the image?"


async def test_approve_and_publish_shortcut(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin6@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve-and-publish",
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "published"


async def test_product_manager_approval_policy_blocks_plain_creator(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin7@example.com")
    creator = await register_and_login(client, email_service, "content-creator7@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    await _add_product_member(client, admin, product_id, creator, "creator")

    patch_resp = await client.patch(
        f"/api/v1/products/{product_id}",
        json={"approval_policy": "product_manager_approval"},
        headers=admin["headers"],
    )
    assert patch_resp.status_code == 200, patch_resp.text

    concept_id = await _generate_one_concept(client, admin, product_id)

    blocked = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
        headers=creator["headers"],
    )
    assert blocked.status_code == 403

    # Company admin always has authority regardless of policy.
    allowed = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
        headers=admin["headers"],
    )
    assert allowed.status_code == 200


async def test_product_manager_approval_policy_allows_product_manager(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin8@example.com")
    manager = await register_and_login(client, email_service, "content-manager8@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    await _add_product_member(client, admin, product_id, manager, "product_manager")

    await client.patch(
        f"/api/v1/products/{product_id}",
        json={"approval_policy": "product_manager_approval"},
        headers=admin["headers"],
    )

    concept_id = await _generate_one_concept(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
        headers=manager["headers"],
    )
    assert resp.status_code == 200, resp.text


async def test_dashboard_counts_reflect_status_changes(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "content-admin9@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    concept_id = await _generate_one_concept(client, admin, product_id)

    before = await client.get(
        f"/api/v1/products/{product_id}/dashboard", headers=admin["headers"]
    )
    assert before.status_code == 200
    assert before.json()["drafts"] == 1
    assert before.json()["published"] == 0

    await client.post(
        f"/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve-and-publish",
        headers=admin["headers"],
    )

    after = await client.get(
        f"/api/v1/products/{product_id}/dashboard", headers=admin["headers"]
    )
    assert after.json()["drafts"] == 0
    assert after.json()["published"] == 1
