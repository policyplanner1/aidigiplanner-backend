"""add suggested_posting_time to creative_concepts

Revision ID: ee09617c8bb6
Revises: 25e2e11eb06c
Create Date: 2026-08-31 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'ee09617c8bb6'
down_revision: Union[str, Sequence[str], None] = '25e2e11eb06c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'creative_concepts',
        sa.Column(
            'suggested_posting_time',
            sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('creative_concepts', 'suggested_posting_time')
