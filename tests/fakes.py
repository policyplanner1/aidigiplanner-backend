from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SocialPlatform
from app.modules.email.base import EmailService
from app.modules.social_accounts.oauth import ConnectedSocialProfile
from app.modules.storage.base import StorageService


class RecordingEmailService(EmailService):
    """Test double: records what was sent to each address instead of
    logging it, so tests can complete a verify/reset/provisioning/approval
    flow without scraping console output."""

    def __init__(self) -> None:
        self.verification_otps: dict[str, str] = {}
        self.password_reset_otps: dict[str, str] = {}
        self.new_member_credentials: dict[str, str] = {}
        self.approved_companies: dict[str, str] = {}
        self.rejected_companies: dict[str, tuple[str, str]] = {}
        self.suspended_companies: dict[str, tuple[str, str]] = {}
        self.deleted_companies: dict[str, str] = {}
        self.demo_requests: list[dict[str, Any]] = []

    async def send_verification_otp(self, *, to_email: str, otp: str) -> None:
        self.verification_otps[to_email] = otp

    async def send_password_reset_email(self, *, to_email: str, otp: str) -> None:
        self.password_reset_otps[to_email] = otp

    async def send_new_member_credentials(
        self, *, to_email: str, temporary_password: str, company_name: str
    ) -> None:
        self.new_member_credentials[to_email] = temporary_password

    async def send_company_approved_email(self, *, to_email: str, company_name: str) -> None:
        self.approved_companies[to_email] = company_name

    async def send_company_rejected_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        self.rejected_companies[to_email] = (company_name, reason)

    async def send_company_suspended_email(
        self, *, to_email: str, company_name: str, reason: str
    ) -> None:
        self.suspended_companies[to_email] = (company_name, reason)

    async def send_company_deleted_email(self, *, to_email: str, company_name: str) -> None:
        self.deleted_companies[to_email] = company_name

    async def send_demo_request(
        self,
        *,
        to_email: str,
        name: str,
        work_email: str,
        company: str,
        message: str,
        ip_address: str | None = None,
    ) -> None:
        self.demo_requests.append(
            {
                "to_email": to_email,
                "name": name,
                "work_email": work_email,
                "company": company,
                "message": message,
                "ip_address": ip_address,
            }
        )


class InMemoryStorageService(StorageService):
    """Test double: keeps asset bytes in a dict instead of touching disk, so
    creative-generation tests don't need a real storage root."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def save(self, *, key: str, data: bytes, content_type: str = "") -> str:
        self.objects[key] = data
        return key

    async def read(self, key: str) -> bytes:
        return self.objects[key]

    async def url_for(self, key: str) -> str:
        return key

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class _FakeArqJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class FakeArqPool:
    """Test double for arq.ArqRedis: runs the enqueued worker function
    inline instead of round-tripping through a real Redis broker, so HTTP
    integration tests exercise the same task code with no live Redis
    server needed in CI.

    Stashes the test's own db_session and storage service on
    ctx["session"]/ctx["storage"] so the worker function sees the job
    through the same savepoint-scoped session the HTTP request used to
    create it, and persists assets to the same in-memory store an
    HTTP-layer download call can read back from -- rather than opening a
    second real DB connection / writing to real disk that dependency
    overrides can't reach (the worker calls get_storage_service() directly,
    not through FastAPI's DI)."""

    def __init__(self, session: AsyncSession, storage: StorageService) -> None:
        self._session = session
        self._storage = storage
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> _FakeArqJob:
        from app.modules.creatives.worker import WORKER_FUNCTIONS

        self.enqueued.append((function, args))
        ctx = {"session": self._session, "storage": self._storage}
        await WORKER_FUNCTIONS[function](ctx, *args)
        return _FakeArqJob(job_id=f"fake-{len(self.enqueued)}")


def default_connected_facebook() -> ConnectedSocialProfile:
    return ConnectedSocialProfile(
        platform=SocialPlatform.facebook,
        handle="Brand Page",
        profile_url="https://facebook.com/111222333",
        external_account_id="111222333",
        access_token="fb-page-token",
        refresh_token=None,
        token_expires_at=None,
        auth0_user_id="meta|100000",
        provider_metadata={"provider": "meta", "page_id": "111222333"},
    )


def default_connected_instagram() -> ConnectedSocialProfile:
    return ConnectedSocialProfile(
        platform=SocialPlatform.instagram,
        handle="@connected_brand",
        profile_url="https://instagram.com/connected_brand",
        external_account_id="17841400000000000",
        access_token="ig-access-token",
        refresh_token=None,
        token_expires_at=None,
        auth0_user_id="instagram|17841400000000000",
        provider_metadata={"provider": "instagram"},
    )


def default_connected_youtube() -> ConnectedSocialProfile:
    return ConnectedSocialProfile(
        platform=SocialPlatform.youtube,
        handle="@brandchannel",
        profile_url="https://youtube.com/@brandchannel",
        external_account_id="UC1234567890ABCDEFGHIJKL",
        access_token="yt-access-token",
        refresh_token="yt-refresh-token",
        token_expires_at=None,
        auth0_user_id="",
        provider_metadata={"provider": "google", "title": "My YouTube Channel"},
    )


def default_connected_google_business() -> ConnectedSocialProfile:
    return ConnectedSocialProfile(
        platform=SocialPlatform.google,
        handle="Brand Store Pune",
        profile_url="https://maps.google.com/?cid=123",
        external_account_id="9876543210",
        access_token="gbp-access-token",
        refresh_token="gbp-refresh-token",
        token_expires_at=None,
        auth0_user_id="",
        provider_metadata={"provider": "google_business", "title": "Brand Store Pune"},
    )


class FakeSocialOAuthClient:
    """Test double for Auth0/Meta and Google YouTube. Returns canned profiles
    instead of hitting the network."""

    def __init__(
        self,
        *,
        configured: bool = True,
        profile: ConnectedSocialProfile | None = None,
    ) -> None:
        self.configured = configured
        self.profile = profile or default_connected_instagram()
        self.codes: list[str] = []
        self.profiles = {
            SocialPlatform.instagram: self.profile
            if self.profile.platform is SocialPlatform.instagram
            else default_connected_instagram(),
            SocialPlatform.facebook: (
                self.profile
                if self.profile.platform is SocialPlatform.facebook
                else default_connected_facebook()
            ),
            SocialPlatform.youtube: (
                self.profile
                if self.profile.platform is SocialPlatform.youtube
                else default_connected_youtube()
            ),
            SocialPlatform.google: (
                self.profile
                if self.profile.platform is SocialPlatform.google
                else default_connected_google_business()
            ),
        }

    def is_configured(self) -> bool:
        return self.configured

    def is_configured_for(self, platform: SocialPlatform) -> bool:
        return self.configured and platform in {
            SocialPlatform.instagram,
            SocialPlatform.facebook,
            SocialPlatform.youtube,
            SocialPlatform.google,
        }

    def build_authorize_url(self, *, platform: SocialPlatform, state: str) -> str:
        if platform in {SocialPlatform.youtube, SocialPlatform.google}:
            return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"
        if platform is SocialPlatform.facebook:
            return f"https://www.facebook.com/v21.0/dialog/oauth?state={state}"
        return (
            "https://auth0.test/authorize?response_type=code"
            f"&connection={platform.value}&state={state}"
        )

    async def complete_authorization(
        self, *, platform: SocialPlatform, code: str
    ) -> ConnectedSocialProfile:
        self.codes.append(code)
        if not self.configured:
            from app.core.exceptions import BadRequestError

            raise BadRequestError(
                "Social account OAuth is not configured.", code="oauth_not_configured"
            )
        profile = self.profiles.get(platform) or self.profile
        if profile is None:
            from app.core.exceptions import BadRequestError

            raise BadRequestError(
                "No social account was found.",
                code="social_account_not_found",
            )
        return profile

