"""add blueprint governance policy"""
from alembic import op
import sqlalchemy as sa

revision = 'd4a6f201e7c8'
down_revision = 'c9e1f4276b5d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('blueprints', sa.Column('requires_approval', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('blueprints', sa.Column('recovery_policy', sa.String(length=32), server_default='preserve', nullable=False))


def downgrade():
    op.drop_column('blueprints', 'recovery_policy')
    op.drop_column('blueprints', 'requires_approval')
