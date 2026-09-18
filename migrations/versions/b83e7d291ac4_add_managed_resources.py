"""add provider-neutral managed resources"""
from alembic import op
import sqlalchemy as sa

revision = 'b83e7d291ac4'
down_revision = 'a72df0169c31'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'managed_resources',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('resource_type', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=512), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('primary_ip', sa.String(length=64), nullable=True),
        sa.Column('lifecycle_status', sa.String(length=16), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('destroyed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('deployment_id'),
    )
    op.create_index(op.f('ix_managed_resources_deployment_id'), 'managed_resources', ['deployment_id'])
    op.create_index(op.f('ix_managed_resources_lifecycle_status'), 'managed_resources', ['lifecycle_status'])
    op.create_index(op.f('ix_managed_resources_provider'), 'managed_resources', ['provider'])
    op.create_index(op.f('ix_managed_resources_provider_id'), 'managed_resources', ['provider_id'])
    op.create_index(op.f('ix_managed_resources_resource_type'), 'managed_resources', ['resource_type'])


def downgrade():
    op.drop_index(op.f('ix_managed_resources_resource_type'), table_name='managed_resources')
    op.drop_index(op.f('ix_managed_resources_provider_id'), table_name='managed_resources')
    op.drop_index(op.f('ix_managed_resources_provider'), table_name='managed_resources')
    op.drop_index(op.f('ix_managed_resources_lifecycle_status'), table_name='managed_resources')
    op.drop_index(op.f('ix_managed_resources_deployment_id'), table_name='managed_resources')
    op.drop_table('managed_resources')
