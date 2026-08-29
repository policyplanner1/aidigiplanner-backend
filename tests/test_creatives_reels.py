import io
from typing import Any

from httpx import AsyncClient
from PIL import Image

from tests.factories import register_and_login
from tests.fakes import FakeArqPool, RecordingEmailService


async def _create_product(client: AsyncClient, admin: dict, name: str = "Reel Product") -> str:
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


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color="#123456").save(buf, format="PNG")
    return buf.getvalue()


async def _upload_avatar(client: AsyncClient, admin: dict, product_id: str) -> None:
    resp = await client.put(
        f"/api/v1/products/{product_id}/brand-profile/avatar",
        files={"file": ("avatar.png", _tiny_png(), "image/png")},
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["avatar_mime_type"] == "image/png"


def _generate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_line": "travel",
        "topic": "Monsoon travel safety tips",
        "format": "reel",
        "concept_count": 1,
    }
    payload.update(overrides)
    return payload


async def test_generate_story_reel_stops_at_awaiting_render_with_no_assets(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin1@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202, generate_resp.text
    job = generate_resp.json()
    assert job["status"] == "awaiting_render"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 1
    assert concepts[0]["reel_script"] is not None
    assert concepts[0]["assets"] == []


async def test_render_assets_produces_clips_and_final_reel(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin1b@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]

    render_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
        headers=admin["headers"],
    )
    assert render_resp.status_code == 202, render_resp.text
    assert render_resp.json()["status"] == "succeeded"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    assert len(concepts) == 1
    labels = sorted(a["label"] for a in concepts[0]["assets"])
    # cover (Sub-phase E's static image), 3 scene clips, and the final
    # assembled reel.
    assert labels == ["cover", "reel", "scene_01", "scene_02", "scene_03"]

    kinds = {a["label"]: a["kind"] for a in concepts[0]["assets"]}
    assert kinds["cover"] == "image"
    assert kinds["scene_01"] == "raw_clip"
    assert kinds["reel"] == "video"

    reel_asset_id = next(a["id"] for a in concepts[0]["assets"] if a["label"] == "reel")
    download_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives/assets/{reel_asset_id}/download",
        headers=admin["headers"],
    )
    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "video/mp4"
    assert len(download_resp.content) > 0


async def test_render_assets_on_post_job_400s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin1c@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(format="post"),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]
    assert generate_resp.json()["status"] == "succeeded"

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert "not awaiting render" in resp.json()["error"]["message"]


async def test_render_assets_twice_on_same_reel_job_400s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin1d@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(),
        headers=admin["headers"],
    )
    job_id = generate_resp.json()["id"]

    first = await client.post(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
        headers=admin["headers"],
    )
    assert first.status_code == 202
    assert first.json()["status"] == "succeeded"

    second = await client.post(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
        headers=admin["headers"],
    )
    assert second.status_code == 400
    assert "not awaiting render" in second.json()["error"]["message"]


async def test_generate_avatar_reel_without_avatar_400s(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin2@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(reel_style="avatar"),
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert "avatar" in resp.json()["error"]["message"].lower()
    assert arq_pool.enqueued == []


async def test_upload_avatar_then_generate_avatar_reel_succeeds(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin3@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    await _upload_avatar(client, admin, product_id)

    generate_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(reel_style="avatar"),
        headers=admin["headers"],
    )
    assert generate_resp.status_code == 202, generate_resp.text
    assert generate_resp.json()["status"] == "awaiting_render"
    job_id = generate_resp.json()["id"]

    render_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
        headers=admin["headers"],
    )
    assert render_resp.status_code == 202, render_resp.text
    assert render_resp.json()["status"] == "succeeded"

    list_resp = await client.get(
        f"/api/v1/products/{product_id}/creatives",
        headers=admin["headers"],
    )
    concepts = list_resp.json()
    labels = sorted(a["label"] for a in concepts[0]["assets"])
    assert labels == ["cover", "reel", "scene_01", "scene_02", "scene_03"]

    avatar_download = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/avatar", headers=admin["headers"]
    )
    assert avatar_download.status_code == 200
    assert avatar_download.headers["content-type"] == "image/png"


async def test_story_and_avatar_reels_are_distinct_jobs(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin4@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)
    await _upload_avatar(client, admin, product_id)

    story_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(reel_style="story"),
        headers=admin["headers"],
    )
    avatar_resp = await client.post(
        f"/api/v1/products/{product_id}/creatives/generate",
        json=_generate_payload(reel_style="avatar"),
        headers=admin["headers"],
    )
    assert story_resp.json()["id"] != avatar_resp.json()["id"]
    assert len(arq_pool.enqueued) == 2


async def test_get_avatar_404s_when_none_uploaded(
    client: AsyncClient, email_service: RecordingEmailService, arq_pool: FakeArqPool
) -> None:
    admin = await register_and_login(client, email_service, "reel-admin5@example.com")
    product_id = await _create_product(client, admin)
    await _set_brand_profile(client, admin, product_id)

    resp = await client.get(
        f"/api/v1/products/{product_id}/brand-profile/avatar", headers=admin["headers"]
    )
    assert resp.status_code == 404
