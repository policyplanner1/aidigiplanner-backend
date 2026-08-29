"""company onboarding state

Revision ID: 5a6290e330cb
Revises: ab84dbaa5c86
Create Date: 2026-08-28 23:24:35.841381

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a6290e330cb'
down_revision: Union[str, Sequence[str], None] = 'ab84dbaa5c86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default on onboarding_step backfills pre-existing rows to
    # "registered" (the safe assumption -- there's no way to know how far a
    # company that predates this column actually got through onboarding),
    # then is dropped so the column matches the model.
    op.add_column('companies', sa.Column('brand_structure', sa.Enum('single_brand', 'multi_brand', 'unsure', name='companybrandstructure', native_enum=False, create_constraint=True, length=32), nullable=True))
    op.add_column('companies', sa.Column('onboarding_step', sa.Enum('registered', 'email_verified', 'brand_structure_selected', 'brand_profile_completed', 'first_product_created', 'completed', name='companyonboardingstep', native_enum=False, create_constraint=True, length=32), nullable=False, server_default='registered'))
    op.add_column('companies', sa.Column('group_website_url', sa.String(length=500), nullable=True))
    op.add_column('companies', sa.Column('group_logo_storage_key', sa.String(length=500), nullable=True))
    op.add_column('companies', sa.Column('group_logo_mime_type', sa.String(length=128), nullable=True))
    op.alter_column('companies', 'onboarding_step', server_default=None)
    # ### end Alembic commands ###

    # Autogenerate also flagged every enum-backed CHECK constraint in the
    # schema as "removed" here -- the same MariaDB CHECK-constraint
    # reflection quirk noted in prior migrations, not a real diff.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('companies', 'group_logo_mime_type')
    op.drop_column('companies', 'group_logo_storage_key')
    op.drop_column('companies', 'group_website_url')
    op.drop_column('companies', 'onboarding_step')
    op.drop_column('companies', 'brand_structure')
