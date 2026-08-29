"""remove campaigns table and repoint creative briefs to project

Revision ID: 637984130cd0
Revises: 7473bcd6200e
Create Date: 2026-08-22 15:37:44.383925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '637984130cd0'
down_revision: Union[str, Sequence[str], None] = '7473bcd6200e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('creative_briefs', sa.Column('project_id', sa.CHAR(length=36), nullable=False))
    op.create_index(op.f('ix_creative_briefs_project_id'), 'creative_briefs', ['project_id'], unique=False)
    op.create_foreign_key(op.f('fk_creative_briefs_project_id_projects'), 'creative_briefs', 'projects', ['project_id'], ['id'])
    op.drop_constraint(op.f('fk_creative_briefs_campaign_id_campaigns'), 'creative_briefs', type_='foreignkey')
    op.drop_index(op.f('ix_creative_briefs_campaign_id'), table_name='creative_briefs')
    op.drop_column('creative_briefs', 'campaign_id')

    op.drop_constraint(op.f('fk_generation_jobs_campaign_id_campaigns'), 'generation_jobs', type_='foreignkey')
    op.drop_index(op.f('ix_generation_jobs_campaign_id'), table_name='generation_jobs')
    op.drop_column('generation_jobs', 'campaign_id')

    op.drop_table('campaigns')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('campaigns',
    sa.Column('project_id', mysql.CHAR(length=36), nullable=False),
    sa.Column('name', mysql.VARCHAR(length=255), nullable=False),
    sa.Column('goal', mysql.TEXT(), nullable=True),
    sa.Column('target_audience', mysql.TEXT(), nullable=True),
    sa.Column('product_or_service', mysql.VARCHAR(length=255), nullable=True),
    sa.Column('platforms', mysql.LONGTEXT(charset='utf8mb4', collation='utf8mb4_bin'), nullable=False),
    sa.Column('start_date', sa.DATE(), nullable=True),
    sa.Column('end_date', sa.DATE(), nullable=True),
    sa.Column('status', mysql.VARCHAR(length=32), nullable=False),
    sa.Column('created_by', mysql.CHAR(length=36), nullable=False),
    sa.Column('id', mysql.CHAR(length=36), nullable=False),
    sa.Column('created_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('updated_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('deleted_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.CheckConstraint("`status` in ('draft','active','completed','archived')", name=op.f('ck_campaigns_campaignstatus')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_campaigns_created_by_users')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_campaigns_project_id_projects')),
    sa.PrimaryKeyConstraint('id'),
    mysql_collate='utf8mb4_general_ci',
    mysql_default_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index(op.f('ix_campaigns_project_id'), 'campaigns', ['project_id'], unique=False)

    op.add_column('generation_jobs', sa.Column('campaign_id', mysql.CHAR(length=36), nullable=False))
    op.create_index(op.f('ix_generation_jobs_campaign_id'), 'generation_jobs', ['campaign_id'], unique=False)
    op.create_foreign_key(op.f('fk_generation_jobs_campaign_id_campaigns'), 'generation_jobs', 'campaigns', ['campaign_id'], ['id'])

    op.add_column('creative_briefs', sa.Column('campaign_id', mysql.CHAR(length=36), nullable=False))
    op.create_index(op.f('ix_creative_briefs_campaign_id'), 'creative_briefs', ['campaign_id'], unique=False)
    op.create_foreign_key(op.f('fk_creative_briefs_campaign_id_campaigns'), 'creative_briefs', 'campaigns', ['campaign_id'], ['id'])
    op.drop_constraint(op.f('fk_creative_briefs_project_id_projects'), 'creative_briefs', type_='foreignkey')
    op.drop_index(op.f('ix_creative_briefs_project_id'), table_name='creative_briefs')
    op.drop_column('creative_briefs', 'project_id')
