import io
from typing import Any

from httpx import AsyncClient
from PIL import Image

from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Brand Product") -> str:
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


def _brand_payload(name: str = "PolicyPlanner") -> dict[str, Any]:
    return {
        "name": name,
        "category": "Insurance",
        "market": "India",
        "audience_primary": "Young professionals, 25-40",
        "audience_secondary": "Small business owners",
        "tone": ["confident", "reassuring"],
        "languages": ["en", "hi"],
        "voice": "Confident, plain-language, never salesy.",
        "pillars": ["education", "trust", "convenience"],
        "website_url": "https://policyplanner.example.com",
        "domains": ["policyplanner.example.com"],
        "knowledge_notes": ["Claims settlement ratio is 98% for FY24."],
        "knowledge_urls": ["https://policyplanner.example.com/faq"],
        "ai_instructions": "Always mention the 24/7 claims helpline.",
        "visual_identity": {
            "palette": ["#0057B8", "#FFFFFF"],
            "heading_font": "Inter",
            "body_font": "Inter",
            "style_keywords": ["clean", "trustworthy"],
            "avoid": ["clutter"],
        },
        "compliance_mandatory_disclaimer": "Terms and conditions apply.",
        "compliance_secondary_disclaimers": ["Subject to underwriting."],
        "compliance_banned_claims": ["guaranteed returns"],
        "compliance_rules": ["Always show the disclaimer."],
        "cta_bank": ["Get a quote", "Learn more"],
        "hashtag_bank": ["#insurance", "#travel"],
        "product_lines": [
            {
                "id": "travel",
                "label": "Travel Insurance",
                "partners": ["PartnerCo"],
                "hooks": ["Never travel unprotected"],
            }
        ],
    }


async def test_get_brand_profile_404_when_unset(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin1@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile", headers=admin["headers"]
    )
    assert resp.status_code == 404


async def test_product_manager_can_create_and_read_brand_profile(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin2@example.com")
    product_id = await _create_product(client, admin)

    put_resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(),
        headers=admin["headers"],
    )
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["name"] == "PolicyPlanner"
    assert body["product_lines"][0]["id"] == "travel"
    assert body["voice"] == "Confident, plain-language, never salesy."
    assert body["pillars"] == ["education", "trust", "convenience"]
    assert body["website_url"] == "https://policyplanner.example.com"
    assert body["domains"] == ["policyplanner.example.com"]
    assert body["knowledge_notes"] == ["Claims settlement ratio is 98% for FY24."]
    assert body["knowledge_urls"] == ["https://policyplanner.example.com/faq"]
    assert body["ai_instructions"] == "Always mention the 24/7 claims helpline."
    assert body["visual_identity"]["heading_font"] == "Inter"
    assert body["visual_identity"]["body_font"] == "Inter"
    assert body["logo_mime_type"] is None
    assert body["dark_logo_mime_type"] is None
    assert body["icon_mime_type"] is None

    get_resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile", headers=admin["headers"]
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == body["id"]


async def test_upsert_is_idempotent_per_product(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin3@example.com")
    product_id = await _create_product(client, admin)

    first = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload("First Name"),
        headers=admin["headers"],
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    second = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload("Renamed"),
        headers=admin["headers"],
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.json()["name"] == "Renamed"


async def test_creator_cannot_write_brand_profile_but_can_read(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin4@example.com")
    creator = await register_and_login(client, email_service, "brand-editor4@example.com")
    product_id = await _create_product(client, admin)
    await _add_product_member(client, admin, product_id, creator, "creator")

    await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(),
        headers=admin["headers"],
    )

    creator_write = await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload("Editor Attempt"),
        headers=creator["headers"],
    )
    assert creator_write.status_code == 403

    creator_read = await client.get(
        f"/api/v1/products/{product_id}/brand-profile", headers=creator["headers"]
    )
    assert creator_read.status_code == 200


async def test_outsider_gets_404_on_brand_profile(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin5@example.com")
    outsider = await register_and_login(client, email_service, "brand-outsider5@example.com")
    product_id = await _create_product(client, admin)

    resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile", headers=outsider["headers"]
    )
    assert resp.status_code == 404


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color="#123456").save(buf, format="PNG")
    return buf.getvalue()


async def test_upload_and_download_logo_dark_logo_and_icon(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin6@example.com")
    product_id = await _create_product(client, admin)
    await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(),
        headers=admin["headers"],
    )

    for slug in ("logo", "logo-dark", "icon"):
        upload_resp = await client.put(
            f"/api/v1/products/{product_id}/brand-profile/{slug}",
            files={"file": (f"{slug}.png", _tiny_png(), "image/png")},
            headers=admin["headers"],
        )
        assert upload_resp.status_code == 200, upload_resp.text

        download_resp = await client.get(
            f"/api/v1/products/{product_id}/brand-profile/{slug}", headers=admin["headers"]
        )
        assert download_resp.status_code == 200
        assert download_resp.headers["content-type"] == "image/png"
        assert download_resp.content.startswith(b"\x89PNG")

    profile = (
        await client.get(
            f"/api/v1/products/{product_id}/brand-profile", headers=admin["headers"]
        )
    ).json()
    assert profile["logo_mime_type"] == "image/png"
    assert profile["dark_logo_mime_type"] == "image/png"
    assert profile["icon_mime_type"] == "image/png"


async def test_download_logo_404s_when_none_uploaded(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "brand-admin7@example.com")
    product_id = await _create_product(client, admin)
    await client.put(
        f"/api/v1/products/{product_id}/brand-profile",
        json=_brand_payload(),
        headers=admin["headers"],
    )

    resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/logo", headers=admin["headers"]
    )
    assert resp.status_code == 404
