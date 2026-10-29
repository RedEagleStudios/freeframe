"""streaming projects: projects.streaming_mode, asset_versions.ready_at, media_files.raw_deleted_at

A streaming project keeps a web MP4 plus the HLS ladder and deletes each
version's uploaded original some days after the version became ready.
`ready_at` records that moment; versions already ready are backfilled with
their `created_at` (asset_versions has no updated_at). `raw_deleted_at` is set
only after the original was actually deleted.

Revision ID: d7e2b5c8a913
Revises: c3d9a1f7e2b4
Create Date: 2026-10-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd7e2b5c8a913'
down_revision: Union[str, Sequence[str], None] = 'c3d9a1f7e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("streaming_mode", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("asset_versions", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media_files", sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE asset_versions SET ready_at = created_at "
        "WHERE processing_status = 'ready' AND ready_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("media_files", "raw_deleted_at")
    op.drop_column("asset_versions", "ready_at")
    op.drop_column("projects", "streaming_mode")
