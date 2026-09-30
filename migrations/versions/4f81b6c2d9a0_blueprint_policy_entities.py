"""add Policy Engine entity ACLs to Blueprints

Revision ID: 4f81b6c2d9a0
Revises: e7c2a91f4b63
"""
from alembic import op
import sqlalchemy as sa


revision = '4f81b6c2d9a0'
down_revision = 'e7c2a91f4b63'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'blueprints',
        sa.Column(
            'allowed_entities',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        'user_project_contexts',
        sa.Column('entity_key', sa.String(length=160), nullable=True),
    )


def downgrade():
    op.drop_column('user_project_contexts', 'entity_key')
    op.drop_column('blueprints', 'allowed_entities')
