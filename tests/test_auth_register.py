from httpx import AsyncClient

from tests.factories import DEFAULT_PASSWORD, register_user
from tests.fakes import RecordingEmailService


async def test_register_creates_pending_user_and_company_admin(client: AsyncClient) -> None:
    body = await register_user(client, "founder@example.com", company_name="Acme Inc")

    assert body["user"]["email"] == "founder@example.com"
    assert body["user"]["status"] == "pending"
    assert body["user"]["email_verified_at"] is None
    assert body["company"]["name"] == "Acme Inc"
    assert body["company"]["slug"] == "acme-inc"
    assert body["company"]["status"] == "pending_approval"


async def test_register_sends_verification_email(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "founder2@example.com")
    assert "founder2@example.com" in email_service.verification_otps


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    await register_user(client, "dupe@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "dupe@example.com",
            "password": DEFAULT_PASSWORD,
            "full_name": "Someone Else",
            "company_name": "Another Co",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_register_duplicate_email_case_insensitive(client: AsyncClient) -> None:
    await register_user(client, "casesensitive@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "CaseSensitive@Example.com",
            "password": DEFAULT_PASSWORD,
            "full_name": "Someone Else",
            "company_name": "Another Co",
        },
    )
    assert resp.status_code == 409


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "shortpw@example.com",
            "password": "short1",
            "full_name": "Someone",
            "company_name": "Some Co",
        },
    )
    assert resp.status_code == 422


async def test_register_rejects_invalid_email(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": DEFAULT_PASSWORD,
            "full_name": "Someone",
            "company_name": "Some Co",
        },
    )
    assert resp.status_code == 422


async def test_register_generates_unique_slug_on_collision(client: AsyncClient) -> None:
    first = await register_user(
        client, "a@example.com", company_name="Collide Co", full_name="A"
    )
    second = await register_user(
        client, "b@example.com", company_name="Collide Co", full_name="B"
    )
    assert first["company"]["slug"] == "collide-co"
    assert second["company"]["slug"] == "collide-co-2"
