"""add instance_auth_settings (Sign in with Discord)

One row, created by the first PUT /admin/auth/discord. The client secret is
stored as a Fernet token keyed from JWT_SECRET, never in plain text.

Revision ID: 9b4e1f7a2c35
Revises: 7c2e9a4b1d60
Create Date: 2026-10-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '9b4e1f7a2c35'
down_revision: Union[str, Sequence[str], None] = '7c2e9a4b1d60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_auth_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("discord_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("discord_client_id", sa.String(length=255), nullable=True),
        sa.Column("discord_client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("discord_server_id", sa.String(length=32), nullable=True),
        sa.Column("discord_allowed_role_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("instance_auth_settings")
