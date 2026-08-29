from typing import Any

from httpx import AsyncClient

from app.db.mixins import utcnow
from app.models.company import Company
from app.models.enums import CompanyStatus
from tests.fakes import RecordingEmailService

DEFAULT_PASSWORD = "correct-horse-battery"


async def register_user(
    client: AsyncClient,
    email: str,
    *,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test User",
    company_name: str = "Test Co",
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "company_name": company_name,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def verify_user(
    client: AsyncClient, email_service: RecordingEmailService, email: str
) -> None:
    otp = email_service.verification_otps[email]
    resp = await client.post("/api/v1/auth/verify-email", json={"email": email, "otp": otp})
    assert resp.status_code == 200, resp.text


async def login(
    client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, Any]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def approve_company(client: AsyncClient, company_id: str) -> None:
    """Flips a company straight to `active` via the same db_session the app
    is using (stashed on `client` in conftest.py), bypassing the real
    Super-Admin-approves-over-HTTP flow. Most tests aren't exercising
    approval itself — they just need a company that's already usable."""
    session = client.db_session  # type: ignore[attr-defined]
    company = await session.get(Company, company_id)
    company.status = CompanyStatus.active
    company.approved_at = utcnow()
    await session.commit()


async def register_and_login(
    client: AsyncClient,
    email_service: RecordingEmailService,
    email: str,
    *,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test User",
    company_name: str = "Test Co",
) -> dict[str, Any]:
    """Registers, verifies, auto-approves the company, logs in — the common
    setup nearly every test needs. Returns user/company ids plus a
    ready-to-use auth header."""
    reg = await register_user(
        client, email, password=password, full_name=full_name, company_name=company_name
    )
    await verify_user(client, email_service, email)
    await approve_company(client, reg["company"]["id"])
    tokens = await login(client, email, password)
    return {
        "user_id": reg["user"]["id"],
        "email": email.lower(),
        "company_id": reg["company"]["id"],
        "company_slug": reg["company"]["slug"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }
