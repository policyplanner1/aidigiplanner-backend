from datetime import UTC, datetime

from sqlalchemy import CHAR, DateTime
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7


def utcnow() -> datetime:
    # Naive on purpose: MySQL DATETIME columns carry no timezone, and
    # aiomysql/pymysql hand back naive datetimes on read. Every timestamp
    # in this app is UTC by convention, never timezone-aware in Python —
    # mixing the two here would make every expiry comparison in service
    # code raise TypeError against values just loaded from the DB.
    return datetime.now(UTC).replace(tzinfo=None)


def new_uuid7() -> str:
    return str(uuid7())


# DATETIME(6) on MySQL, plain DateTime elsewhere (e.g. SQLite in unit tests
# that don't touch the DB layer). Always UTC, timezone-naive at storage time.
TIMESTAMP_TYPE = DateTime(timezone=False).with_variant(MySQLDateTime(fsp=6), "mysql")


class UUIDPKMixin:
    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=new_uuid7, insert_default=new_uuid7
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, insert_default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        default=utcnow,
        insert_default=utcnow,
        onupdate=utcnow,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)


class CreatedAtMixin:
    """For append-only/token-style tables that have no updated_at column."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, insert_default=utcnow
    )
