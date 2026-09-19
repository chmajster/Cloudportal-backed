from alembic import op
import sqlalchemy as sa

revision = 'b42d8e1f6a53'
down_revision = 'a31c7d9e5f42'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('auth_source', sa.String(length=16), nullable=False, server_default='local'))
    op.add_column('users', sa.Column('external_id', sa.String(length=1024), nullable=True))
    op.create_index('ix_users_auth_source', 'users', ['auth_source'], unique=False)
    op.create_index('uq_users_external_id', 'users', ['external_id'], unique=True)


def downgrade():
    op.drop_index('uq_users_external_id', table_name='users')
    op.drop_index('ix_users_auth_source', table_name='users')
    op.drop_column('users', 'external_id')
    op.drop_column('users', 'auth_source')
