"""create sub_products table

Revision ID: 307528e2fbc4
Revises: 4e9b89e80b3e
Create Date: 2026-08-28 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '307528e2fbc4'
down_revision: Union[str, Sequence[str], None] = '4e9b89e80b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'sub_products',
        sa.Column('product_id', sa.CHAR(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'active', 'archived', name='productstatus',
                native_enum=False, create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            'branding_mode',
            sa.Enum(
                'use_product_branding', 'separate_brand', name='subproductbrandingmode',
                native_enum=False, create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column('created_by', sa.CHAR(length=36), nullable=False),
        sa.Column('id', sa.CHAR(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.Column('deleted_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_sub_products_product_id_products')),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_sub_products_created_by_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sub_products')),
        sa.UniqueConstraint('product_id', 'slug', name='uq_sub_products_product_slug'),
    )
    op.create_index(op.f('ix_sub_products_product_id'), 'sub_products', ['product_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('sub_products')
