"""add project_links

Named links to outside pages (an Outline doc, say) shown in a project's folder
tree. A new table, so no existing data changes.

Revision ID: f4b8d2e6a1c3
Revises: d7e2b5c8a913
Create Date: 2026-11-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f4b8d2e6a1c3'
down_revision: Union[str, Sequence[str], None] = 'd7e2b5c8a913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'project_links',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('folder_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('folders.id'), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('url', sa.String(length=2000), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_project_links_folder_id', 'project_links', ['folder_id'])
    op.create_index('ix_project_links_project_folder', 'project_links', ['project_id', 'folder_id'])


def downgrade() -> None:
    op.drop_index('ix_project_links_project_folder', table_name='project_links')
    op.drop_index('ix_project_links_folder_id', table_name='project_links')
    op.drop_table('project_links')
