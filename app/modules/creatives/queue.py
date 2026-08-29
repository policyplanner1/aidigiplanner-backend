from typing import Annotated

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from fastapi import Depends

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    """One process-wide Redis connection pool for enqueueing jobs from the
    request path (lazily created -- a connection at import time would make
    every test/script that imports this module require a live Redis).

    Tests override this dependency with a fake that runs the enqueued
    worker function inline instead of round-tripping through a real broker
    (see tests/fakes.py FakeArqPool), so the HTTP-level generation flow is
    exercised without a live Redis server in CI.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


ArqPoolDep = Annotated[ArqRedis, Depends(get_arq_pool)]
