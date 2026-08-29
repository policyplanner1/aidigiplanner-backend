import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Protocol

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceededError


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Record one hit for `key`; raise RateLimitExceededError if it now
        exceeds `limit` hits within the trailing `window_seconds`."""
        ...


class InMemoryRateLimiter:
    """Sliding-window limiter. Fine for a single process; swap for a
    Redis-backed implementation of the same Protocol once this runs behind
    more than one worker/instance."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimitExceededError("Too many requests. Please try again later.")
            bucket.append(now)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return InMemoryRateLimiter()


def rate_limit_by_ip(scope: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: limits requests per client IP for the given `scope`
    (e.g. "login", "register") using settings.rate_limit_per_ip_per_minute."""

    async def dependency(request: Request) -> None:
        settings = get_settings()
        ip = request.client.host if request.client else "unknown"
        await get_rate_limiter().hit(
            f"ip:{scope}:{ip}",
            limit=settings.rate_limit_per_ip_per_minute,
            window_seconds=60,
        )

    return dependency


async def rate_limit_by_email(email: str, scope: str) -> None:
    """Called directly by routers/services once the request body is parsed
    (email isn't available to a plain path/query FastAPI dependency) using
    settings.rate_limit_per_email_per_minute."""
    settings = get_settings()
    await get_rate_limiter().hit(
        f"email:{scope}:{email.lower()}",
        limit=settings.rate_limit_per_email_per_minute,
        window_seconds=60,
    )
