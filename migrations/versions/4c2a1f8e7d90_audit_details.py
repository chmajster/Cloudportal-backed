"""Add structured audit details for Proxmox Admin.

Revision ID: 4c2a1f8e7d90
Revises: f9d6c2a81e44
"""
from alembic import op
import sqlalchemy as sa

revision = '4c2a1f8e7d90'
down_revision = 'f9d6c2a81e44'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'audit',
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade():
    op.drop_column('audit', 'details')
