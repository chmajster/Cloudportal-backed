"""require initial administrator password change"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e9a421c8d3'
down_revision = '6ff55df4f169'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column('users', 'must_change_password')
