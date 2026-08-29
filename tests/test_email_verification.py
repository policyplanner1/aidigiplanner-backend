from httpx import AsyncClient

from tests.factories import register_user
from tests.fakes import RecordingEmailService


async def test_verify_email_activates_pending_user(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "verifyme@example.com")
    otp = email_service.verification_otps["verifyme@example.com"]

    resp = await client.post(
        "/api/v1/auth/verify-email", json={"email": "verifyme@example.com", "otp": otp}
    )
    assert resp.status_code == 200


async def test_verify_email_otp_is_single_use(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "singleuseverify@example.com")
    otp = email_service.verification_otps["singleuseverify@example.com"]

    first = await client.post(
        "/api/v1/auth/verify-email", json={"email": "singleuseverify@example.com", "otp": otp}
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/verify-email", json={"email": "singleuseverify@example.com", "otp": otp}
    )
    assert second.status_code == 400


async def test_verify_email_invalid_otp_rejected(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "wrongcode@example.com")

    resp = await client.post(
        "/api/v1/auth/verify-email", json={"email": "wrongcode@example.com", "otp": "000000"}
    )
    assert resp.status_code == 400


async def test_verify_email_unknown_account_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/verify-email", json={"email": "noaccount@example.com", "otp": "123456"}
    )
    assert resp.status_code == 400


async def test_resend_verification_same_response_for_real_and_fake_email(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "resendme@example.com")
    # The register call already sent one verification code; grab a fresh
    # one to prove resend actually issues a new one.
    email_service.verification_otps.pop("resendme@example.com")

    real = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "resendme@example.com"}
    )
    fake = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "noaccount@example.com"}
    )

    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()
    assert "resendme@example.com" in email_service.verification_otps
    assert "noaccount@example.com" not in email_service.verification_otps


async def test_resend_verification_noop_for_already_verified_user(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "alreadyverified@example.com")
    otp = email_service.verification_otps["alreadyverified@example.com"]
    await client.post(
        "/api/v1/auth/verify-email", json={"email": "alreadyverified@example.com", "otp": otp}
    )
    email_service.verification_otps.pop("alreadyverified@example.com")

    resp = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "alreadyverified@example.com"}
    )
    assert resp.status_code == 200
    assert "alreadyverified@example.com" not in email_service.verification_otps
