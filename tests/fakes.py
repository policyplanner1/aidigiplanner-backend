from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.email.base import EmailService
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
