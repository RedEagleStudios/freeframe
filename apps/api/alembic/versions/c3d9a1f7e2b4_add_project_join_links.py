"""add project_join_links and users.discord_gate_exempt

Single-use project join links: only a SHA-256 of the token is stored. An
account created through one is exempt from the Discord server/role gate.

Revision ID: c3d9a1f7e2b4
Revises: 9b4e1f7a2c35
Create Date: 2026-10-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c3d9a1f7e2b4'
down_revision: Union[str, Sequence[str], None] = '9b4e1f7a2c35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("discord_gate_exempt", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "project_join_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        # The projectrole type already exists (project_members.role).
        sa.Column(
            "role",
            postgresql.ENUM("owner", "editor", "reviewer", "viewer", name="projectrole", create_type=False),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["used_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_join_links_project_id", "project_join_links", ["project_id"])
    op.create_index("ix_project_join_links_token_hash", "project_join_links", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_project_join_links_token_hash", table_name="project_join_links")
    op.drop_index("ix_project_join_links_project_id", table_name="project_join_links")
    op.drop_table("project_join_links")
    op.drop_column("users", "discord_gate_exempt")
