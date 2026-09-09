"""add heygen avatar id to brand profiles

Revision ID: 1d8c2113c37c
Revises: ee09617c8bb6
Create Date: 2026-09-08 16:16:24.794051

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1d8c2113c37c'
down_revision: Union[str, Sequence[str], None] = 'ee09617c8bb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('brand_profiles', sa.Column('heygen_avatar_id', sa.String(length=255), nullable=True))

    # Autogenerate also flagged every enum-backed CHECK constraint in the
    # schema as "removed" here - a MariaDB CHECK-constraint reflection
    # quirk (see the prior migrations, e.g. 7473bcd6200e), not a real diff.
    # None of those enums changed in this revision, so left untouched.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('brand_profiles', 'heygen_avatar_id')
