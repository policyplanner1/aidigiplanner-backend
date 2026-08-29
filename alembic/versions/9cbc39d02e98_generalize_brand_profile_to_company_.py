"""generalize brand profile to company product sub_product scope

Revision ID: 9cbc39d02e98
Revises: 93f679352044
Create Date: 2026-08-28 22:35:40.408458

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '9cbc39d02e98'
down_revision: Union[str, Sequence[str], None] = '93f679352044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # scope/owner_id added nullable first, backfilled from the existing
    # product_id (every pre-existing row is implicitly scope='product'),
    # then tightened to NOT NULL -- MySQL can't add a NOT NULL column with
    # no default onto a table that already has rows.
    op.add_column('brand_profiles', sa.Column('scope', sa.Enum('company', 'product', 'sub_product', name='brandanalysisscope', native_enum=False, create_constraint=True, length=32), nullable=True))
    op.add_column('brand_profiles', sa.Column('owner_id', sa.CHAR(length=36), nullable=True))
    op.execute("UPDATE brand_profiles SET scope = 'product', owner_id = product_id")
    op.alter_column('brand_profiles', 'scope', existing_type=sa.Enum('company', 'product', 'sub_product', name='brandanalysisscope', native_enum=False, create_constraint=True, length=32), nullable=False)
    op.alter_column('brand_profiles', 'owner_id', existing_type=sa.CHAR(length=36), nullable=False)
    # FK must go before the indexes that back it (MySQL 1553) -- the
    # matching bare-column drop_index below covers the FK's own auto-index.
    op.drop_constraint(op.f('fk_brand_profiles_product_id_products'), 'brand_profiles', type_='foreignkey')
    op.drop_index(op.f('ix_brand_profiles_product_id'), table_name='brand_profiles')
    op.drop_index(op.f('uq_brand_profiles_product_id'), table_name='brand_profiles')
    op.create_index(op.f('ix_brand_profiles_owner_id'), 'brand_profiles', ['owner_id'], unique=False)
    op.create_index(op.f('ix_brand_profiles_scope'), 'brand_profiles', ['scope'], unique=False)
    op.create_unique_constraint('uq_brand_profiles_scope_owner_id', 'brand_profiles', ['scope', 'owner_id'])
    op.drop_column('brand_profiles', 'product_id')
    # server_default here only exists to backfill pre-existing rows -- it's
    # dropped right after so the column matches the model (Python-side
    # default only, same convention as every other enum column here).
    op.add_column('products', sa.Column('branding_mode', sa.Enum('use_company_branding', 'separate_brand', name='productbrandingmode', native_enum=False, create_constraint=True, length=32), nullable=False, server_default='separate_brand'))
    op.add_column('products', sa.Column('approval_policy', sa.Enum('no_approval', 'one_approver', 'product_manager_approval', 'company_admin_approval', name='contentapprovalpolicy', native_enum=False, create_constraint=True, length=32), nullable=False, server_default='no_approval'))
    op.alter_column('products', 'branding_mode', server_default=None)
    op.alter_column('products', 'approval_policy', server_default=None)
    # ### end Alembic commands ###

    # Autogenerate also flagged every other enum-backed CHECK constraint in
    # the schema as "removed" here -- the same MariaDB CHECK-constraint
    # reflection quirk noted in prior migrations, not a real diff.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'approval_policy')
    op.drop_column('products', 'branding_mode')
    op.add_column('brand_profiles', sa.Column('product_id', mysql.CHAR(length=36), nullable=True))
    op.execute("UPDATE brand_profiles SET product_id = owner_id WHERE scope = 'product'")
    # company/sub_product-scoped rows have no product to go back to -- this
    # is a best-effort downgrade, not a lossless one.
    op.execute("DELETE FROM brand_profiles WHERE scope != 'product'")
    op.create_foreign_key(op.f('fk_brand_profiles_product_id_products'), 'brand_profiles', 'products', ['product_id'], ['id'])
    op.alter_column('brand_profiles', 'product_id', existing_type=mysql.CHAR(length=36), nullable=False)
    op.drop_constraint('uq_brand_profiles_scope_owner_id', 'brand_profiles', type_='unique')
    op.drop_index(op.f('ix_brand_profiles_scope'), table_name='brand_profiles')
    op.drop_index(op.f('ix_brand_profiles_owner_id'), table_name='brand_profiles')
    op.create_index(op.f('uq_brand_profiles_product_id'), 'brand_profiles', ['product_id'], unique=True)
    op.create_index(op.f('ix_brand_profiles_product_id'), 'brand_profiles', ['product_id'], unique=False)
    op.drop_column('brand_profiles', 'owner_id')
    op.drop_column('brand_profiles', 'scope')
