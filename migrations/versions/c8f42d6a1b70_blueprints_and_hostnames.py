"""blueprints and hostname manager"""
from alembic import op
import sqlalchemy as sa

revision = 'c8f42d6a1b70'
down_revision = 'b7e9a421c8d3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('source', sa.String(length=32), server_default='API', nullable=False))
    op.add_column('audit', sa.Column('source', sa.String(length=32), server_default='API', nullable=False))
    op.create_table(
        'hostname_schemes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('pattern', sa.String(length=255), nullable=False),
        sa.Column('next_number', sa.Integer(), nullable=False),
        sa.Column('padding', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'blueprints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=63), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('visibility', sa.JSON(), nullable=False),
        sa.Column('allowed_role_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_user_ids', sa.JSON(), nullable=False),
        sa.Column('variables_schema', sa.JSON(), nullable=False),
        sa.Column('deployment', sa.JSON(), nullable=False),
        sa.Column('workflow', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_table(
        'hostname_reservations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('scheme_id', sa.Integer(), nullable=False),
        sa.Column('hostname', sa.String(length=253), nullable=False),
        sa.Column('values', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('resource_id', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['scheme_id'], ['hostname_schemes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hostname'),
    )
    op.create_index(op.f('ix_hostname_reservations_hostname'), 'hostname_reservations', ['hostname'])
    op.create_index(op.f('ix_hostname_reservations_scheme_id'), 'hostname_reservations', ['scheme_id'])
    op.create_index(op.f('ix_hostname_reservations_status'), 'hostname_reservations', ['status'])


def downgrade():
    op.drop_index(op.f('ix_hostname_reservations_status'), table_name='hostname_reservations')
    op.drop_index(op.f('ix_hostname_reservations_scheme_id'), table_name='hostname_reservations')
    op.drop_index(op.f('ix_hostname_reservations_hostname'), table_name='hostname_reservations')
    op.drop_table('hostname_reservations')
    op.drop_table('blueprints')
    op.drop_table('hostname_schemes')
    op.drop_column('audit', 'source')
    op.drop_column('jobs', 'source')
