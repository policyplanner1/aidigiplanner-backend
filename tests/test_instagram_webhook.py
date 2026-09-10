import hashlib
import hmac
import json

from httpx import AsyncClient

from app.core.config import get_settings


async def test_instagram_webhook_verify_returns_challenge(client: AsyncClient) -> None:
    token = get_settings().meta_webhook_verify_token
    resp = await client.get(
        "/api/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": token,
            "hub.challenge": "meta-challenge-123",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "meta-challenge-123"


async def test_instagram_webhook_verify_rejects_wrong_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "meta-challenge-123",
        },
    )
    assert resp.status_code == 403


async def test_instagram_webhook_post_accepts_signed_payload(client: AsyncClient) -> None:
    secret = get_settings().meta_app_secret
    body = json.dumps({"object": "instagram", "entry": [{"id": "1"}]}).encode("utf-8")
    if not secret:
        resp = await client.post(
            "/api/webhooks/instagram",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        return
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    resp = await client.post(
        "/api/webhooks/instagram",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest}",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
