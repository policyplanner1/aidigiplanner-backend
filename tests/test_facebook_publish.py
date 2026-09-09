from urllib.parse import parse_qs, urlparse

import httpx
from httpx import AsyncClient

from app.modules.social_accounts.publish import default_test_logo_png
from tests.factories import register_and_login
from tests.fakes import FakeSocialOAuthClient, RecordingEmailService
from tests.test_social_oauth import _create_product


class _FakeGraphResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _patch_graph_post(monkeypatch, posted: dict[str, object], payload: dict[str, object]) -> None:
    original_post = httpx.AsyncClient.post
    original_get = httpx.AsyncClient.get

    async def fake_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "/photos" in str(url):
            posted["url"] = url
            posted["data"] = kwargs.get("data")
            posted["files"] = kwargs.get("files")
            return _FakeGraphResponse(200, payload)
        return await original_post(self, url, *args, **kwargs)

    async def fake_get(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "debug_token" in str(url):
            return _FakeGraphResponse(
                200,
                {
                    "data": {
                        "is_valid": True,
                        "type": "PAGE",
                        "scopes": ["pages_show_list", "pages_manage_posts"],
                    }
                },
            )
        if "/me/accounts" in str(url):
            return _FakeGraphResponse(
                200,
                {
                    "data": [
                        {
                            "id": "111222333",
                            "name": "Brand Page",
                            "access_token": "fb-page-token",
                        }
                    ]
                },
            )
        if "graph.facebook.com" in str(url):
            return _FakeGraphResponse(200, {"id": "111222333", "access_token": "fb-page-token"})
        return await original_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


async def _connect_facebook(
    client: AsyncClient, admin: dict[str, object]
) -> tuple[str, str]:
    product_id = await _create_product(client, admin, "Facebook Publish Product")
    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/facebook",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    await client.get("/api/social/facebook/callback", params={"code": "fb-code", "state": state})
    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    account_id = listed.json()[0]["id"]
    return product_id, account_id


async def test_facebook_test_post_uploads_logo(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
    monkeypatch,
) -> None:
    posted: dict[str, object] = {}
    _patch_graph_post(monkeypatch, posted, {"id": "photo_1", "post_id": "111222333_444"})
    admin = await register_and_login(client, email_service, "fb-publish-admin@example.com")
    product_id, account_id = await _connect_facebook(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/{account_id}/test-post",
        headers=admin["headers"],
        files={"file": ("logo.png", default_test_logo_png(), "image/png")},
        data={"caption": "Test post from AI Social Planner"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["photo_id"] == "photo_1"
    assert body["post_id"] == "111222333_444"
    assert "facebook.com" in body["permalink"]
    assert posted["data"]["access_token"] == "fb-page-token"
    assert posted["data"]["message"] == "Test post from AI Social Planner"
    assert posted["data"]["published"] == "true"
    assert str(posted["url"]).endswith("/111222333/photos")
    assert posted["files"]["source"][0] == "logo.png"


async def test_facebook_test_post_uses_default_logo_when_no_file(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
    monkeypatch,
) -> None:
    posted: dict[str, object] = {}
    _patch_graph_post(monkeypatch, posted, {"id": "photo_2", "post_id": "111222333_555"})
    admin = await register_and_login(client, email_service, "fb-publish-default@example.com")
    product_id, account_id = await _connect_facebook(client, admin)

    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/{account_id}/test-post",
        headers=admin["headers"],
        data={"caption": "Default logo post"},
    )
    assert resp.status_code == 200
    assert posted["files"]["source"][0] == "logo.png"


async def test_facebook_test_post_rejects_manual_account(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    admin = await register_and_login(client, email_service, "fb-publish-manual@example.com")
    product_id = await _create_product(client, admin, "Manual Social Product")
    created = await client.post(
        f"/api/v1/products/{product_id}/social-accounts",
        json={"platform": "facebook", "handle": "manual.page"},
        headers=admin["headers"],
    )
    assert created.status_code == 201
    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/{created.json()['id']}/test-post",
        headers=admin["headers"],
    )
    assert resp.status_code == 400


async def test_facebook_test_post_rejects_instagram(
    client: AsyncClient,
    email_service: RecordingEmailService,
    oauth_client: FakeSocialOAuthClient,
) -> None:
    admin = await register_and_login(client, email_service, "fb-publish-ig@example.com")
    product_id = await _create_product(client, admin, "IG Publish Product")
    start = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/oauth/instagram",
        json={},
        headers=admin["headers"],
    )
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    await client.get("/api/social/instagram/callback", params={"code": "ig-code", "state": state})
    listed = await client.get(
        f"/api/v1/products/{product_id}/social-accounts", headers=admin["headers"]
    )
    ig_id = next(row["id"] for row in listed.json() if row["platform"] == "instagram")
    resp = await client.post(
        f"/api/v1/products/{product_id}/social-accounts/{ig_id}/test-post",
        headers=admin["headers"],
    )
    assert resp.status_code == 400
    assert "Facebook" in resp.json()["error"]["message"]
