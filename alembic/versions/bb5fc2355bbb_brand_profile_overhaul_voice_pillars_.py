"""brand profile overhaul: voice, pillars, knowledge base, real image uploads

Revision ID: bb5fc2355bbb
Revises: 637984130cd0
Create Date: 2026-08-22 15:48:38.271913

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'bb5fc2355bbb'
down_revision: Union[str, Sequence[str], None] = '637984130cd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('brand_profiles', sa.Column('voice', sa.Text(), nullable=False))
    op.add_column('brand_profiles', sa.Column('pillars', sa.JSON(), nullable=False))
    op.add_column('brand_profiles', sa.Column('website_url', sa.String(length=500), nullable=True))
    op.add_column('brand_profiles', sa.Column('domains', sa.JSON(), nullable=False))
    op.add_column('brand_profiles', sa.Column('knowledge_notes', sa.JSON(), nullable=False))
    op.add_column('brand_profiles', sa.Column('knowledge_urls', sa.JSON(), nullable=False))
    op.add_column('brand_profiles', sa.Column('ai_instructions', sa.Text(), nullable=False))
    op.add_column('brand_profiles', sa.Column('logo_storage_key', sa.String(length=500), nullable=True))
    op.add_column('brand_profiles', sa.Column('logo_mime_type', sa.String(length=128), nullable=True))
    op.add_column('brand_profiles', sa.Column('dark_logo_storage_key', sa.String(length=500), nullable=True))
    op.add_column('brand_profiles', sa.Column('dark_logo_mime_type', sa.String(length=128), nullable=True))
    op.add_column('brand_profiles', sa.Column('icon_storage_key', sa.String(length=500), nullable=True))
    op.add_column('brand_profiles', sa.Column('icon_mime_type', sa.String(length=128), nullable=True))
    op.drop_column('brand_profiles', 'logo_path')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('brand_profiles', sa.Column('logo_path', mysql.VARCHAR(length=500), nullable=True))
    op.drop_column('brand_profiles', 'icon_mime_type')
    op.drop_column('brand_profiles', 'icon_storage_key')
    op.drop_column('brand_profiles', 'dark_logo_mime_type')
    op.drop_column('brand_profiles', 'dark_logo_storage_key')
    op.drop_column('brand_profiles', 'logo_mime_type')
    op.drop_column('brand_profiles', 'logo_storage_key')
    op.drop_column('brand_profiles', 'ai_instructions')
    op.drop_column('brand_profiles', 'knowledge_urls')
    op.drop_column('brand_profiles', 'knowledge_notes')
    op.drop_column('brand_profiles', 'domains')
    op.drop_column('brand_profiles', 'website_url')
    op.drop_column('brand_profiles', 'pillars')
    op.drop_column('brand_profiles', 'voice')
