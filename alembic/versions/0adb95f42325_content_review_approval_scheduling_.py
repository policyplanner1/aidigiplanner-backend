"""content review approval scheduling lifecycle

Revision ID: 0adb95f42325
Revises: 5a6290e330cb
Create Date: 2026-08-28 23:40:16.320408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '0adb95f42325'
down_revision: Union[str, Sequence[str], None] = '5a6290e330cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('content_comments',
    sa.Column('concept_id', sa.CHAR(length=36), nullable=False),
    sa.Column('author_id', sa.CHAR(length=36), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_content_comments_author_id_users')),
    sa.ForeignKeyConstraint(['concept_id'], ['creative_concepts.id'], name=op.f('fk_content_comments_concept_id_creative_concepts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_comments'))
    )
    op.create_index(op.f('ix_content_comments_concept_id'), 'content_comments', ['concept_id'], unique=False)

    # server_default on the new NOT NULL creative_briefs columns backfills
    # pre-existing rows, then is dropped so the columns match the model.
    op.add_column('creative_briefs', sa.Column('platforms', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('creative_briefs', sa.Column('sub_product_id', sa.CHAR(length=36), nullable=True))
    op.add_column('creative_briefs', sa.Column('objective', sa.Text(), nullable=False, server_default=''))
    op.add_column('creative_briefs', sa.Column('offer', sa.Text(), nullable=False, server_default=''))
    op.add_column('creative_briefs', sa.Column('festival_occasion', sa.String(length=120), nullable=False, server_default=''))
    op.add_column('creative_briefs', sa.Column('audience_override', sa.Text(), nullable=True))
    op.add_column('creative_briefs', sa.Column('tone_override', sa.JSON(), nullable=True))
    op.add_column('creative_briefs', sa.Column('cta_override', sa.String(length=255), nullable=True))
    op.add_column('creative_briefs', sa.Column('reference_image_storage_key', sa.String(length=500), nullable=True))
    op.add_column('creative_briefs', sa.Column('publishing_date', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))
    op.alter_column('creative_briefs', 'platforms', server_default=None)
    op.alter_column('creative_briefs', 'objective', server_default=None)
    op.alter_column('creative_briefs', 'offer', server_default=None)
    op.alter_column('creative_briefs', 'festival_occasion', server_default=None)
    op.create_foreign_key(op.f('fk_creative_briefs_sub_product_id_sub_products'), 'creative_briefs', 'sub_products', ['sub_product_id'], ['id'])

    # creative_concepts.status replaces review_status -- added nullable,
    # backfilled from the old column's values (pending -> draft is the only
    # real remap; approved/rejected carry over as-is), then tightened.
    op.add_column('creative_concepts', sa.Column('status', sa.Enum('draft', 'in_review', 'approved', 'rejected', 'scheduled', 'published', name='contentstatus', native_enum=False, create_constraint=True, length=32), nullable=True))
    op.execute("UPDATE creative_concepts SET status = CASE review_status WHEN 'pending' THEN 'draft' ELSE review_status END")
    op.alter_column(
        'creative_concepts', 'status',
        existing_type=sa.Enum('draft', 'in_review', 'approved', 'rejected', 'scheduled', 'published', name='contentstatus', native_enum=False, create_constraint=True, length=32),
        nullable=False,
    )
    op.add_column('creative_concepts', sa.Column('scheduled_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))
    op.add_column('creative_concepts', sa.Column('published_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))
    op.drop_constraint(op.f('ck_creative_concepts_conceptreviewstatus'), 'creative_concepts', type_='check')
    op.drop_column('creative_concepts', 'review_status')

    # Autogenerate also flagged every other enum-backed CHECK constraint in
    # the schema as "removed" here -- the same MariaDB CHECK-constraint
    # reflection quirk noted in prior migrations, not a real diff.


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('creative_concepts', sa.Column('review_status', mysql.VARCHAR(length=32), nullable=True))
    op.execute(
        "UPDATE creative_concepts SET review_status = CASE status "
        "WHEN 'draft' THEN 'pending' WHEN 'in_review' THEN 'pending' "
        "WHEN 'scheduled' THEN 'approved' WHEN 'published' THEN 'approved' "
        "ELSE status END"
    )
    op.alter_column('creative_concepts', 'review_status', existing_type=mysql.VARCHAR(length=32), nullable=False)
    op.create_check_constraint(op.f('ck_creative_concepts_conceptreviewstatus'), 'creative_concepts', "`review_status` in ('pending','approved','rejected')")
    op.drop_column('creative_concepts', 'published_at')
    op.drop_column('creative_concepts', 'scheduled_at')
    op.drop_column('creative_concepts', 'status')
    op.drop_constraint(op.f('fk_creative_briefs_sub_product_id_sub_products'), 'creative_briefs', type_='foreignkey')
    op.drop_column('creative_briefs', 'publishing_date')
    op.drop_column('creative_briefs', 'reference_image_storage_key')
    op.drop_column('creative_briefs', 'cta_override')
    op.drop_column('creative_briefs', 'tone_override')
    op.drop_column('creative_briefs', 'audience_override')
    op.drop_column('creative_briefs', 'festival_occasion')
    op.drop_column('creative_briefs', 'offer')
    op.drop_column('creative_briefs', 'objective')
    op.drop_column('creative_briefs', 'sub_product_id')
    op.drop_column('creative_briefs', 'platforms')
    op.drop_table('content_comments')
