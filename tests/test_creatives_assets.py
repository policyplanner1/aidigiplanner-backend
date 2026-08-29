from typing import Any

from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Asset Product") -> str:
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
        "concept_count": 2,
    }
    payload.update(overrides)
    return payload


async def test_generate_post_creates_one_image_asset_per_concept(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "asset-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(format="post", concept_count=2),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202
    assert generate_resp.json()["status"] == "succeeded"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 2
    for concept in concepts:
        assert len(concept["assets"]) == 1
        assert concept["assets"][0]["label"] == "post"
        assert concept["assets"][0]["kind"] == "image"
        assert concept["assets"][0]["mime_type"] == "image/png"


async def test_generate_carousel_creates_one_asset_per_slide(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "asset-admin2@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(format="carousel", carousel_slides=4, concept_count=1),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 1
    assets = concepts[0]["assets"]
    assert len(assets) == 4
    assert sorted(a["label"] for a in assets) == ["slide_01", "slide_02", "slide_03", "slide_04"]


async def test_rejected_concept_has_no_assets(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "asset-admin3@example.com")
    product_id = await _create_product(client, admin)
    # Deterministically fails compliance for every concept (see
    # test_creatives_generation.py's compliance test for why this works).
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
        "compliance_banned_claims": ["travel insurance"],
        "compliance_rules": [],
        "cta_bank": ["Get a quote"],
        "hashtag_bank": ["#insurance", "#travel"],
        "product_lines": [
            {
                "id": "travel",
                "label": "Travel Insurance",
                "partners": [],
                "hooks": ["flight cancelled"],
            }
        ],
    }
    resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile", json=payload, headers=admin["headers"]
    )
    assert resp.status_code == 200

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=1),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 1
    assert concepts[0]["compliance_rejected"] is True
    assert concepts[0]["assets"] == []


async def test_download_asset_returns_image_bytes(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "asset-admin4@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=1),
        headers=admin["headers"],
    )
    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    asset_id = list_resp.json()[0]["assets"][0]["id"]

    download_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives/assets/{asset_id}/download",
        headers=admin["headers"],
    )
    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "image/png"
    assert download_resp.content.startswith(b"\x89PNG")


async def test_download_asset_from_wrong_product_404s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "asset-admin5@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    other_product_id = await _create_product(client, admin, name="Other Asset Product")

    await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(concept_count=1),
        headers=admin["headers"],
    )
    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    asset_id = list_resp.json()[0]["assets"][0]["id"]

    resp = await client.get(
        f"/api/v1/products/{other_product_id}/creatives/assets/{asset_id}/download",
        headers=admin["headers"],
    )
    assert resp.status_code == 404
