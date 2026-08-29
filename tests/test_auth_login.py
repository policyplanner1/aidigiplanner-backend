from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import UserStatus
from app.models.user import User
from tests.factories import (
    DEFAULT_PASSWORD,
    approve_company,
    register_and_login,
    register_user,
    verify_user,
)
from tests.fakes import RecordingEmailService


async def test_login_success_returns_token_pair(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    reg = await register_user(client, "login-ok@example.com")
    await verify_user(client, email_service, "login-ok@example.com")
    await approve_company(client, reg["company"]["id"])

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "login-ok@example.com", "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == get_settings().access_token_ttl_minutes * 60


async def test_login_blocked_before_email_verification(client: AsyncClient) -> None:
    await register_user(client, "pending@example.com")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "pending@example.com", "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_not_verified"


async def test_login_wrong_password_rejected(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "wrongpw@example.com")
    await verify_user(client, email_service, "wrongpw@example.com")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrongpw@example.com", "password": "totally-wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_login_nonexistent_email_same_response_as_wrong_password(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "realuser@example.com")
    await verify_user(client, email_service, "realuser@example.com")

    wrong_password_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "realuser@example.com", "password": "wrong-password-here"},
    )
    nonexistent_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody-here@example.com", "password": "wrong-password-here"},
    )

    assert wrong_password_resp.status_code == nonexistent_resp.status_code == 401
    assert wrong_password_resp.json() == nonexistent_resp.json()


async def test_login_suspended_user_blocked(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    ctx = await register_and_login(client, email_service, "suspend-me@example.com")

    user = await db_session.get(User, ctx["user_id"])
    assert user is not None
    user.status = UserStatus.suspended
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "suspend-me@example.com", "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "account_suspended"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_returns_user_and_company_membership(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(
        client, email_service, "me-check@example.com", company_name="Me Check Co"
    )

    resp = await client.get("/api/v1/auth/me", headers=ctx["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "me-check@example.com"
    assert len(body["companies"]) == 1
    assert body["companies"][0]["role"] == "company_admin"
    assert body["companies"][0]["company_slug"] == "me-check-co"
