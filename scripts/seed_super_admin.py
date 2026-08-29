"""Creates the platform's first Super Admin.

Super Admins are global (is_super_admin=True) and hold no company
membership - that's the only thing that distinguishes them from a regular
user, per the tenancy model. There is deliberately no HTTP endpoint that can
create one; this script, run directly against the database via the service
layer, is the only way.

Usage:
    uv run python scripts/seed_super_admin.py --email admin@example.com
    uv run python scripts/seed_super_admin.py  # prompts for both

Email and password can also come from SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD
env vars, e.g. for non-interactive provisioning.
"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.mixins import utcnow  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.models.enums import UserStatus  # noqa: E402
from app.models.user import User  # noqa: E402


class SeedError(Exception):
    pass


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise SeedError("Password must be at least 10 characters long.")


async def _create_super_admin(
    session: AsyncSession, email: str, password: str, full_name: str
) -> None:
    email = email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))

    if existing is not None:
        if existing.is_super_admin:
            print(f"'{email}' is already a Super Admin (id={existing.id}). Nothing to do.")
            return
        raise SeedError(
            f"A user with email '{email}' already exists and is not a Super Admin. "
            "Refusing to modify an existing account - use a different email, "
            "or promote this one manually if that's really what you want."
        )

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_super_admin=True,
        status=UserStatus.active,
        email_verified_at=utcnow(),
    )
    session.add(user)
    await session.commit()
    print(f"Created Super Admin '{email}' (id={user.id}).")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=os.environ.get("SUPER_ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("SUPER_ADMIN_PASSWORD"))
    parser.add_argument("--full-name", default=os.environ.get("SUPER_ADMIN_NAME", "Super Admin"))
    args = parser.parse_args()

    email = args.email or input("Super Admin email: ").strip()
    password = args.password or getpass.getpass("Super Admin password: ")

    try:
        _validate_password(password)
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await _create_super_admin(session, email, password, args.full_name)
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        # Dispose explicitly (rather than letting connections get garbage
        # collected after the loop closes) - on Windows, a pooled aiomysql
        # connection finalized post-shutdown raises a harmless but noisy
        # "Event loop is closed" error from its __del__.
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
