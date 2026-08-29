from httpx import AsyncClient

from tests.factories import register_and_login
from tests.fakes import RecordingEmailService


async def test_refresh_rotates_and_returns_new_pair(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "refresh-me@example.com")

    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] != ctx["access_token"]
    assert body["refresh_token"] != ctx["refresh_token"]

    # New access token works.
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_reuse_of_rotated_token_revokes_family(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "reuse-me@example.com")

    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert first.status_code == 200
    new_refresh_token = first.json()["refresh_token"]

    # Presenting the already-rotated-away token again: reuse detected.
    reuse = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert reuse.status_code == 401
    assert "reuse" in reuse.json()["error"]["message"].lower()

    # The entire family — including the token just issued from rotation — is
    # now dead too, even though it was never itself misused.
    followup = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_refresh_token}
    )
    assert followup.status_code == 401


async def test_refresh_invalid_token_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-refresh-token"}
    )
    assert resp.status_code == 401


async def test_logout_revokes_presented_refresh_token(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "logout-me@example.com")

    resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": ctx["refresh_token"]},
        headers=ctx["headers"],
    )
    assert resp.status_code == 200

    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert refresh_resp.status_code == 401


async def test_logout_all_invalidates_existing_access_token(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "logout-all@example.com")

    resp = await client.post("/api/v1/auth/logout-all", headers=ctx["headers"])
    assert resp.status_code == 200

    me = await client.get("/api/v1/auth/me", headers=ctx["headers"])
    assert me.status_code == 401

    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert refresh_resp.status_code == 401
