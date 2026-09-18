"""add managed VM inventory"""
from alembic import op
import sqlalchemy as sa

revision = 'e4b6c81a2d09'
down_revision = 'd91a7f5e3c42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'managed_vms',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=True),
        sa.Column('node', sa.String(length=63), nullable=False),
        sa.Column('vm_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('management_mode', sa.String(length=16), nullable=False),
        sa.Column('lifecycle_status', sa.String(length=16), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('destroyed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id']),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('deployment_id'),
        sa.UniqueConstraint('provider_id', 'vm_id'),
    )
    op.create_index(op.f('ix_managed_vms_lifecycle_status'), 'managed_vms', ['lifecycle_status'])
    op.create_index(op.f('ix_managed_vms_provider_id'), 'managed_vms', ['provider_id'])


def downgrade():
    op.drop_index(op.f('ix_managed_vms_provider_id'), table_name='managed_vms')
    op.drop_index(op.f('ix_managed_vms_lifecycle_status'), table_name='managed_vms')
    op.drop_table('managed_vms')
