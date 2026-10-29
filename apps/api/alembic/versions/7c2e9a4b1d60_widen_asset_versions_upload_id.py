"""widen asset_versions.upload_id to TEXT

S3 does not bound the length of a multipart upload id, and Cloudflare R2 hands
out ids longer than 255 characters, so storing one in VARCHAR(255) failed every
`/upload/initiate` on R2 with StringDataRightTruncation (a 500).

Revision ID: 7c2e9a4b1d60
Revises: c5d1f3a7b9e2
Create Date: 2026-10-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '7c2e9a4b1d60'
down_revision: Union[str, Sequence[str], None] = 'c5d1f3a7b9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'asset_versions', 'upload_id',
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # An id that no longer fits can't be kept; NULL is the column's documented
    # "unknown" value, which every consumer already falls back on.
    op.execute("UPDATE asset_versions SET upload_id = NULL WHERE length(upload_id) > 255")
    op.alter_column(
        'asset_versions', 'upload_id',
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
