"""add scoped blueprint approval policy overrides"""
from alembic import op
import sqlalchemy as sa

revision = 'f2c7a91d4e63'
down_revision = 'd3f8c41b72a0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('blueprint_auto_approve_for_executors', sa.Boolean(), nullable=True))
    op.add_column('projects', sa.Column('blueprint_approval_timeout_hours', sa.Integer(), nullable=True))
    op.add_column('blueprints', sa.Column('auto_approve_for_executors', sa.Boolean(), nullable=True))
    op.add_column('blueprints', sa.Column('approval_timeout_hours', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('blueprints', 'approval_timeout_hours')
    op.drop_column('blueprints', 'auto_approve_for_executors')
    op.drop_column('projects', 'blueprint_approval_timeout_hours')
    op.drop_column('projects', 'blueprint_auto_approve_for_executors')
