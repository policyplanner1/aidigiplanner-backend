from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _stamp_utc(value: datetime) -> datetime:
    """The DB stores every timestamp as naive-but-UTC-by-convention (see
    app.db.mixins.utcnow) — MySQL DATETIME columns carry no timezone. A
    naive datetime serializes with no offset suffix, and most clients
    (browser `new Date(...)`, Excel, ...) then read that as local time
    instead of UTC. Stamping UTC tzinfo here, only at the API boundary,
    makes the JSON output unambiguous (e.g. "...+00:00") without touching
    the naive-UTC storage convention used everywhere else in the app."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


UTCDatetime = Annotated[datetime, PlainSerializer(_stamp_utc, return_type=datetime)]
