from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.password_reset_otp import PasswordResetOtp
from tests.factories import (
    DEFAULT_PASSWORD,
    approve_company,
    register_and_login,
    register_user,
    verify_user,
)
from tests.fakes import RecordingEmailService


async def _get_reset_token(
    client: AsyncClient, email_service: RecordingEmailService, email: str
) -> str:
    otp = email_service.password_reset_otps[email]
    resp = await client.post("/api/v1/auth/verify-reset-otp", json={"email": email, "otp": otp})
    assert resp.status_code == 200, resp.text
    return resp.json()["reset_token"]  # type: ignore[no-any-return]


async def test_forgot_password_same_response_for_real_and_fake_email(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "hasaccount@example.com")

    real = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "hasaccount@example.com"}
    )
    fake = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "noaccount@example.com"}
    )

    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()
    assert "hasaccount@example.com" in email_service.password_reset_otps
    assert "noaccount@example.com" not in email_service.password_reset_otps


async def test_reset_password_end_to_end(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    reg = await register_user(client, "resetme@example.com")
    await verify_user(client, email_service, "resetme@example.com")
    await approve_company(client, reg["company"]["id"])

    await client.post("/api/v1/auth/forgot-password", json={"email": "resetme@example.com"})
    reset_token = await _get_reset_token(client, email_service, "resetme@example.com")

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "brand-new-password-123"},
    )
    assert resp.status_code == 200

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "resetme@example.com", "password": DEFAULT_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "resetme@example.com", "password": "brand-new-password-123"},
    )
    assert new_login.status_code == 200


async def test_verify_reset_otp_wrong_code_rejected_and_counts_as_an_attempt(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "wrongotp@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "wrongotp@example.com"})
    real_otp = email_service.password_reset_otps["wrongotp@example.com"]
    wrong_otp = "000000" if real_otp != "000000" else "111111"

    resp = await client.post(
        "/api/v1/auth/verify-reset-otp", json={"email": "wrongotp@example.com", "otp": wrong_otp}
    )
    assert resp.status_code == 400

    # The real code still works — one wrong guess doesn't burn the OTP itself.
    ok = await client.post(
        "/api/v1/auth/verify-reset-otp", json={"email": "wrongotp@example.com", "otp": real_otp}
    )
    assert ok.status_code == 200


async def test_verify_reset_otp_locks_out_after_max_attempts(
    client: AsyncClient, email_service: RecordingEmailService, db_session: AsyncSession
) -> None:
    await register_user(client, "lockout@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "lockout@example.com"})
    real_otp = email_service.password_reset_otps["lockout@example.com"]

    # Drive `attempts` straight to the limit via the DB instead of actually
    # making otp_max_attempts wrong guesses over the API — that many calls
    # in a row would trip the per-email rate limiter first and mask what
    # this test is actually checking.
    record = await db_session.scalar(select(PasswordResetOtp))
    assert record is not None
    record.attempts = get_settings().otp_max_attempts
    await db_session.commit()

    # Even the correct code is rejected once locked out — request a new one.
    locked_out = await client.post(
        "/api/v1/auth/verify-reset-otp", json={"email": "lockout@example.com", "otp": real_otp}
    )
    assert locked_out.status_code == 400


async def test_verify_reset_otp_is_single_use(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "otpsingleuse@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "otpsingleuse@example.com"})
    otp = email_service.password_reset_otps["otpsingleuse@example.com"]

    first = await client.post(
        "/api/v1/auth/verify-reset-otp", json={"email": "otpsingleuse@example.com", "otp": otp}
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/auth/verify-reset-otp", json={"email": "otpsingleuse@example.com", "otp": otp}
    )
    assert second.status_code == 400


async def test_reset_password_token_is_single_use(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "singleuse@example.com")
    await verify_user(client, email_service, "singleuse@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "singleuse@example.com"})
    reset_token = await _get_reset_token(client, email_service, "singleuse@example.com")

    first = await client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "first-new-password-1"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "second-new-password-2"},
    )
    assert second.status_code == 400


async def test_reset_password_invalid_token_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": "not-a-real-token", "new_password": "whatever-password-123"},
    )
    assert resp.status_code == 400


async def test_reset_password_revokes_existing_sessions(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "resetsessions@example.com")

    await client.post(
        "/api/v1/auth/forgot-password", json={"email": "resetsessions@example.com"}
    )
    reset_token = await _get_reset_token(client, email_service, "resetsessions@example.com")
    await client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "another-new-password-1"},
    )

    me = await client.get("/api/v1/auth/me", headers=ctx["headers"])
    assert me.status_code == 401

    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": ctx["refresh_token"]}
    )
    assert refresh_resp.status_code == 401


async def test_change_password_success(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "changepw@example.com")

    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "changed-password-123"},
        headers=ctx["headers"],
    )
    assert resp.status_code == 200

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "changepw@example.com", "password": DEFAULT_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "changepw@example.com", "password": "changed-password-123"},
    )
    assert new_login.status_code == 200


async def test_change_password_wrong_current_password_rejected(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "changepwbad@example.com")

    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "totally-wrong-password", "new_password": "new-password-123"},
        headers=ctx["headers"],
    )
    assert resp.status_code == 401


async def test_change_password_invalidates_current_access_token(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    ctx = await register_and_login(client, email_service, "changepwinval@example.com")

    await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "new-password-456"},
        headers=ctx["headers"],
    )

    me = await client.get("/api/v1/auth/me", headers=ctx["headers"])
    assert me.status_code == 401
