from alembic import op
import sqlalchemy as sa

revision = 'e91c6b2a4d73'
down_revision = '7f3c1a9d4b20'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('blueprints', sa.Column('avatar_id', sa.String(length=63), nullable=True))
    op.create_index('ix_blueprints_avatar_id', 'blueprints', ['avatar_id'], unique=False)


def downgrade():
    op.drop_index('ix_blueprints_avatar_id', table_name='blueprints')
    op.drop_column('blueprints', 'avatar_id')
