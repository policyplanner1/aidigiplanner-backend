"""social account oauth tokens

Revision ID: d4e8a91c2b7f
Revises: ee09617c8bb6
Create Date: 2026-09-03 12:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a91c2b7f"
down_revision: Union[str, Sequence[str], None] = "ee09617c8bb6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("social_accounts", sa.Column("external_account_id", sa.String(length=64), nullable=True))
    op.add_column("social_accounts", sa.Column("access_token_encrypted", sa.Text(), nullable=True))
    op.add_column("social_accounts", sa.Column("refresh_token_encrypted", sa.Text(), nullable=True))
    op.add_column("social_accounts", sa.Column("token_expires_at", sa.DateTime(timezone=False), nullable=True))
    op.add_column("social_accounts", sa.Column("auth0_user_id", sa.String(length=255), nullable=True))
    op.add_column(
        "social_accounts",
        sa.Column("provider_metadata", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.alter_column("social_accounts", "provider_metadata", server_default=None)


def downgrade() -> None:
    op.drop_column("social_accounts", "provider_metadata")
    op.drop_column("social_accounts", "auth0_user_id")
    op.drop_column("social_accounts", "token_expires_at")
    op.drop_column("social_accounts", "refresh_token_encrypted")
    op.drop_column("social_accounts", "access_token_encrypted")
    op.drop_column("social_accounts", "external_account_id")
