"""add previous_refresh_token_hash to oauth_grants

The refresh token a refresh was made with stays usable until the client uses
the access token that refresh issued, so a lost refresh response does not end
the connection. A nullable column, so no existing data changes.

Revision ID: e7a3c9d1f5b2
Revises: d4f8a2c6e1b3
Create Date: 2026-11-04
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e7a3c9d1f5b2'
down_revision: Union[str, Sequence[str], None] = 'd4f8a2c6e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('oauth_grants', sa.Column('previous_refresh_token_hash', sa.String(length=64), nullable=True))
    op.create_index(
        op.f('ix_oauth_grants_previous_refresh_token_hash'), 'oauth_grants', ['previous_refresh_token_hash'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_oauth_grants_previous_refresh_token_hash'), table_name='oauth_grants')
    op.drop_column('oauth_grants', 'previous_refresh_token_hash')
