"""add IPAM pools and allocations"""
from alembic import op
import sqlalchemy as sa

revision = 'd91a7f5e3c42'
down_revision = 'c8f42d6a1b70'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ip_pools',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('cidr', sa.String(length=64), nullable=False),
        sa.Column('gateway', sa.String(length=45), nullable=True),
        sa.Column('dns_servers', sa.JSON(), nullable=False),
        sa.Column('excluded_addresses', sa.JSON(), nullable=False),
        sa.Column('next_offset', sa.BigInteger(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cidr'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'ip_allocations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('pool_id', sa.Integer(), nullable=False),
        sa.Column('address', sa.String(length=45), nullable=False),
        sa.Column('prefix_length', sa.Integer(), nullable=False),
        sa.Column('gateway', sa.String(length=45), nullable=True),
        sa.Column('hostname', sa.String(length=253), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('resource_id', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['pool_id'], ['ip_pools.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ip_allocations_address'), 'ip_allocations', ['address'])
    op.create_index(op.f('ix_ip_allocations_pool_id'), 'ip_allocations', ['pool_id'])
    op.create_index(op.f('ix_ip_allocations_resource_id'), 'ip_allocations', ['resource_id'])
    op.create_index(op.f('ix_ip_allocations_status'), 'ip_allocations', ['status'])


def downgrade():
    op.drop_index(op.f('ix_ip_allocations_status'), table_name='ip_allocations')
    op.drop_index(op.f('ix_ip_allocations_resource_id'), table_name='ip_allocations')
    op.drop_index(op.f('ix_ip_allocations_pool_id'), table_name='ip_allocations')
    op.drop_index(op.f('ix_ip_allocations_address'), table_name='ip_allocations')
    op.drop_table('ip_allocations')
    op.drop_table('ip_pools')
