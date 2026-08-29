"""add awaiting_render generation job status

Revision ID: f407d3b76d34
Revises: bb5fc2355bbb
Create Date: 2026-08-22 15:56:11.315616

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f407d3b76d34'
down_revision: Union[str, Sequence[str], None] = 'bb5fc2355bbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_generation_jobs_generationjobstatus'), 'generation_jobs', type_='check')
    op.create_check_constraint(
        op.f('ck_generation_jobs_generationjobstatus'),
        'generation_jobs',
        "`status` in ('queued','running','awaiting_render','succeeded','failed','partially_failed')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_generation_jobs_generationjobstatus'), 'generation_jobs', type_='check')
    op.create_check_constraint(
        op.f('ck_generation_jobs_generationjobstatus'),
        'generation_jobs',
        "`status` in ('queued','running','succeeded','failed','partially_failed')",
    )
