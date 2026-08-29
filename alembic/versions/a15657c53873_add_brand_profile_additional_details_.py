"""add brand profile additional details fields

Revision ID: a15657c53873
Revises: 9cbc39d02e98
Create Date: 2026-08-28 22:49:03.978666

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a15657c53873'
down_revision: Union[str, Sequence[str], None] = '9cbc39d02e98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default on the NOT NULL text/JSON columns exists only to
    # backfill pre-existing rows -- dropped right after so the columns
    # match the model (Python-side default only).
    op.add_column('brand_profiles', sa.Column('tagline', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('brand_profiles', sa.Column('description', sa.Text(), nullable=False, server_default=''))
    op.add_column('brand_profiles', sa.Column('contact_email', sa.String(length=320), nullable=True))
    op.add_column('brand_profiles', sa.Column('contact_number', sa.String(length=32), nullable=True))
    op.add_column('brand_profiles', sa.Column('social_links', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('brand_profiles', sa.Column('regulatory_category', sa.String(length=255), nullable=False, server_default=''))
    op.alter_column('brand_profiles', 'tagline', server_default=None)
    op.alter_column('brand_profiles', 'description', server_default=None)
    op.alter_column('brand_profiles', 'social_links', server_default=None)
    op.alter_column('brand_profiles', 'regulatory_category', server_default=None)
    # ### end Alembic commands ###

    # Autogenerate also flagged every enum-backed CHECK constraint in the
    # schema as "removed" here -- the same MariaDB CHECK-constraint
    # reflection quirk noted in prior migrations, not a real diff.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('brand_profiles', 'regulatory_category')
    op.drop_column('brand_profiles', 'social_links')
    op.drop_column('brand_profiles', 'contact_number')
    op.drop_column('brand_profiles', 'contact_email')
    op.drop_column('brand_profiles', 'description')
    op.drop_column('brand_profiles', 'tagline')
    # ### end Alembic commands ###
