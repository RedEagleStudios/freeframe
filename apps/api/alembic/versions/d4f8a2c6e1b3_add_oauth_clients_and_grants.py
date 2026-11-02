"""add oauth_clients and oauth_grants

OAuth for the MCP server: clients registered through Dynamic Client
Registration, and each user's approval of a client with the hashes of its
current access and refresh token.

Revision ID: d4f8a2c6e1b3
Revises: f4b8d2e6a1c3
Create Date: 2026-11-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4f8a2c6e1b3'
down_revision: Union[str, Sequence[str], None] = 'f4b8d2e6a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('client_info', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('client_id'),
    )
    op.create_table(
        'oauth_grants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('token_version', sa.Integer(), nullable=False),
        sa.Column('access_token_hash', sa.String(length=64), nullable=False),
        sa.Column('access_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
        sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['oauth_clients.client_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_token_hash'),
        sa.UniqueConstraint('refresh_token_hash'),
    )
    op.create_index(op.f('ix_oauth_grants_user_id'), 'oauth_grants', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_oauth_grants_user_id'), table_name='oauth_grants')
    op.drop_table('oauth_grants')
    op.drop_table('oauth_clients')
