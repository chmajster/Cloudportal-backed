"""add credential lifecycle metadata"""
from alembic import op
import sqlalchemy as sa

revision = 'e5b7a3149d20'
down_revision = 'd4a6f201e7c8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('credentials', sa.Column('expires_at', sa.DateTime(), nullable=True))
    op.add_column('credentials', sa.Column('rotation_due_at', sa.DateTime(), nullable=True))
    op.add_column('credentials', sa.Column('secret_updated_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('credentials', 'secret_updated_at')
    op.drop_column('credentials', 'rotation_due_at')
    op.drop_column('credentials', 'expires_at')
