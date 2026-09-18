"""add schedules and durable webhooks"""
from alembic import op
import sqlalchemy as sa

revision = 'c9e1f4276b5d'
down_revision = 'b83e7d291ac4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scheduled_operations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=False),
        sa.Column('operation', sa.String(length=32), nullable=False),
        sa.Column('next_run_at', sa.DateTime(), nullable=False),
        sa.Column('interval_seconds', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('token_id', sa.Integer(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['token_id'], ['tokens.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_scheduled_operations_deployment_id'), 'scheduled_operations', ['deployment_id'])
    op.create_index(op.f('ix_scheduled_operations_is_active'), 'scheduled_operations', ['is_active'])
    op.create_index(op.f('ix_scheduled_operations_next_run_at'), 'scheduled_operations', ['next_run_at'])

    op.create_table(
        'webhook_endpoints',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('encrypted_secret', sa.LargeBinary(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index(op.f('ix_webhook_endpoints_is_active'), 'webhook_endpoints', ['is_active'])

    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('endpoint_id', sa.String(length=36), nullable=False),
        sa.Column('event', sa.String(length=64), nullable=False),
        sa.Column('resource_id', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhook_deliveries_endpoint_id'), 'webhook_deliveries', ['endpoint_id'])
    op.create_index(op.f('ix_webhook_deliveries_event'), 'webhook_deliveries', ['event'])
    op.create_index(op.f('ix_webhook_deliveries_next_attempt_at'), 'webhook_deliveries', ['next_attempt_at'])
    op.create_index(op.f('ix_webhook_deliveries_resource_id'), 'webhook_deliveries', ['resource_id'])
    op.create_index(op.f('ix_webhook_deliveries_status'), 'webhook_deliveries', ['status'])


def downgrade():
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
    op.drop_table('scheduled_operations')
