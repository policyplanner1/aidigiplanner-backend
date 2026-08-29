from httpx import AsyncClient

from tests.factories import register_user, verify_user
from tests.fakes import RecordingEmailService


async def test_login_rate_limited_per_email(
    client: AsyncClient, email_service: RecordingEmailService
) -> None:
    await register_user(client, "ratelimited@example.com")
    await verify_user(client, email_service, "ratelimited@example.com")

    # Default limit is 5/minute per email (see Settings.rate_limit_per_email_per_minute).
    statuses = []
    for _ in range(6):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "ratelimited@example.com", "password": "wrong-password-here"},
        )
        statuses.append(resp.status_code)

    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
