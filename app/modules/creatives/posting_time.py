"""Phase 18/21's "suggested posting time" -- a deterministic, no-network
heuristic (same spirit as the mock providers), not a real engagement-data
model. There is no analytics/engagement tracking anywhere in this backend
(publishing is state-only -- see CreativeService.publish_concept), so this
picks a plausible next slot from a fixed best-hour-per-platform table rather
than claiming to be data-driven.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.enums import SocialPlatform

# UTC hour-of-day widely cited as a strong engagement window for each
# platform. Not personalized, not timezone-aware -- a starting point the
# admin can always override with a manual schedule instead.
_BEST_HOUR_UTC: dict[SocialPlatform, int] = {
    SocialPlatform.instagram: 11,
    SocialPlatform.facebook: 13,
    SocialPlatform.linkedin: 9,
    SocialPlatform.youtube: 17,
    SocialPlatform.twitter: 8,
    SocialPlatform.google: 10,
}
_DEFAULT_HOUR_UTC = 11


def suggest_posting_time(
    platforms: list[str], now: datetime, *, lead_time: timedelta = timedelta(hours=1)
) -> datetime:
    """Next upcoming occurrence of the earliest-listed platform's best hour,
    at least `lead_time` after `now` (so "today at 11am" isn't suggested at
    11:59am). Falls back to a generic hour when no platform is given or
    recognized."""
    hour = _DEFAULT_HOUR_UTC
    for value in platforms:
        try:
            hour = _BEST_HOUR_UTC[SocialPlatform(value)]
            break
        except ValueError:
            continue

    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate < now + lead_time:
        candidate += timedelta(days=1)
    return candidate
