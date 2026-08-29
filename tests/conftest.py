import os
from collections.abc import AsyncGenerator

# Force every Gemini call in the test suite through the mock providers,
# regardless of what a developer's real .env has configured for local
# manual testing -- matches the documented intent ("Leave empty to use
# deterministic mock providers everywhere ... this is what the test suite
# does", see .env.example) and must run before any Settings/CreativeSettings
# is ever constructed (both read GEMINI_API_KEY at instantiation time, and
# get_creative_settings() is lru_cache'd for the life of the process).
# An actual OS env var (as set here) takes priority over the .env file in
# pydantic-settings' resolution order, so this overrides a real key that's
# only present in .env -- os.environ.pop alone would not (the .env file
# would still supply it).
os.environ["GEMINI_API_KEY"] = ""

import pytest
import pytest_asyncio
from arq import ArqRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.rate_limit import get_rate_limiter
from app.db.session import get_db_session
from app.main import app as fastapi_app
from app.models import Base
from app.modules.creatives.brand import (
    Audience,
    BrandProfileDTO,
    ComplianceProfile,
    ProductLineProfile,
    VisualIdentity,
)
from app.modules.creatives.queue import get_arq_pool
from app.modules.email.base import EmailService
from app.modules.email.provider import get_email_service
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service
from tests.fakes import FakeArqPool, InMemoryStorageService, RecordingEmailService

settings = get_settings()


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(settings.test_database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Wraps each test in an outer transaction + SAVEPOINT. Service code
    calling session.commit() only ends the savepoint (SQLAlchemy's
    join_transaction_mode="create_savepoint" restarts it automatically), so
    the outer transaction — and everything written by the test — is always
    rolled back at teardown. Tests never need to clean up their own rows."""
    connection = await test_engine.connect()
    trans = await connection.begin()
    session = AsyncSession(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )

    yield session

    await session.close()
    await trans.rollback()
    await connection.close()


@pytest.fixture
def email_service() -> RecordingEmailService:
    return RecordingEmailService()


@pytest.fixture
def storage_service() -> InMemoryStorageService:
    return InMemoryStorageService()


@pytest.fixture
def brand() -> BrandProfileDTO:
    """Mirrors the prototype's brand.yaml (a real, already-verified
    compliance/voice profile) so ported pure-unit tests for compliance/
    pricing/postprocess/mock-provider logic exercise realistic data instead
    of throwaway test fixtures."""
    return BrandProfileDTO(
        id="policyplanner",
        name="PolicyPlanner",
        category="Insurance distribution (travel / motor / health)",
        market="India",
        audience=Audience(
            primary="Indian travellers 25-45 booking international trips",
            secondary="Car and bike owners at renewal; POSP agents sourcing policies",
        ),
        tone=["clear", "reassuring", "practical", "never fear-mongering"],
        languages=["en", "hi", "hinglish"],
        product_lines=[
            ProductLineProfile(
                id="travel",
                label="Travel insurance",
                partners=["Bajaj Allianz"],
                hooks=[
                    "visa-mandated cover",
                    "baggage loss",
                    "medical emergency abroad",
                    "trip cancellation",
                    "flight delay compensation",
                ],
            ),
            ProductLineProfile(
                id="motor",
                label="Motor insurance",
                partners=[],
                hooks=[
                    "renewal reminder",
                    "NCB transfer",
                    "own damage vs third party",
                    "zero dep",
                    "roadside assistance",
                ],
            ),
            ProductLineProfile(
                id="health",
                label="Health insurance",
                partners=[],
                hooks=[
                    "room rent limits",
                    "waiting periods",
                    "cashless network",
                    "family floater vs individual",
                    "pre-existing disease cover",
                ],
            ),
        ],
        visual_identity=VisualIdentity(
            palette=["#0A2540", "#1B7F79", "#F5A623", "#FFFFFF"],
            heading_font="modern geometric sans, high-contrast headline",
            body_font="humanist sans, generous leading",
            style_keywords=[
                "clean editorial",
                "soft depth",
                "flat vector accents",
                "airy whitespace",
            ],
            avoid=[
                "stock-photo handshakes and umbrella-over-family clichés",
                "hospital beds, accident wreckage, distressed faces",
                "piles of cash, gold coins",
                "generic AI plastic gloss",
            ],
        ),
        compliance=ComplianceProfile(
            mandatory_disclaimer="Insurance is the subject matter of solicitation.",
            secondary_disclaimers=["T&C apply. Read the policy wording before purchase."],
            banned_claims=[
                "guaranteed claim approval",
                "100% claim settlement",
                "cheapest in India",
                "no documents required",
                "instant claim payout",
                "free insurance",
                "any absolute or superlative claim about coverage, price, or settlement",
            ],
            rules=[
                "Never name a specific premium amount without the words 'starting from' and a "
                "T&C note.",
                "Never imply a claim outcome is certain.",
                "Never compare PolicyPlanner directly against a named competitor.",
                "Insurer/partner names may only appear where explicitly supplied in the brief.",
            ],
        ),
        cta_bank=[
            "Get a quote in 60 seconds — link in bio",
            "DM 'TRAVEL' for a quick quote",
            "Check your renewal date — link in bio",
        ],
        hashtag_bank=[
            "#TravelInsurance",
            "#MotorInsurance",
            "#PolicyPlanner",
            "#InsuranceIndia",
            "#TravelSmart",
        ],
    )


@pytest.fixture
def arq_pool(db_session: AsyncSession, storage_service: InMemoryStorageService) -> FakeArqPool:
    return FakeArqPool(session=db_session, storage=storage_service)


@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limiter() -> None:
    # get_rate_limiter() is a process-wide lru_cache singleton (by design —
    # it's meant to persist across requests). Clear it between tests so one
    # test's login attempts don't trip another test's rate limit.
    limiter = get_rate_limiter()
    limiter._hits.clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    email_service: RecordingEmailService,
    arq_pool: FakeArqPool,
    storage_service: InMemoryStorageService,
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    def _override_get_email_service() -> EmailService:
        return email_service

    def _override_get_arq_pool() -> ArqRedis:
        return arq_pool  # type: ignore[return-value]

    def _override_get_storage_service() -> StorageService:
        return storage_service

    fastapi_app.dependency_overrides[get_db_session] = _override_get_db_session
    fastapi_app.dependency_overrides[get_email_service] = _override_get_email_service
    fastapi_app.dependency_overrides[get_arq_pool] = _override_get_arq_pool
    fastapi_app.dependency_overrides[get_storage_service] = _override_get_storage_service

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Stashed so test helpers (tests/factories.py) can reach the same
        # session the app is using — e.g. to auto-approve a freshly
        # registered company without going through the Super Admin HTTP flow.
        ac.db_session = db_session  # type: ignore[attr-defined]
        yield ac

    fastapi_app.dependency_overrides.clear()
