"""add story and video creative formats

Revision ID: 25e2e11eb06c
Revises: 0adb95f42325
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25e2e11eb06c'
down_revision: Union[str, Sequence[str], None] = '0adb95f42325'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_creative_briefs_creativeformat'), 'creative_briefs', type_='check')
    op.create_check_constraint(
        op.f('ck_creative_briefs_creativeformat'),
        'creative_briefs',
        "`format` in ('post','carousel','reel','story','video')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_creative_briefs_creativeformat'), 'creative_briefs', type_='check')
    op.create_check_constraint(
        op.f('ck_creative_briefs_creativeformat'),
        'creative_briefs',
        "`format` in ('post','carousel','reel')",
    )
