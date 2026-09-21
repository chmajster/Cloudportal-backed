"""add provider-neutral Day-2 action framework"""
from alembic import op
import sqlalchemy as sa

revision = '9d2c4e7a1b60'
down_revision = 'f0a4c2d9b671'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'day2_action_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('resource_id', sa.String(length=36), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approval_state', sa.String(length=24), nullable=False),
        sa.Column('job_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('correlation_id', sa.String(length=36), nullable=False),
        sa.Column('request_id', sa.String(length=36), nullable=False),
        sa.Column('retry_of', sa.String(length=36), nullable=True),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('safe_diff', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id']),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id']),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id']),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id']),
        sa.ForeignKeyConstraint(['retry_of'], ['day2_action_requests.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id'),
    )
    for column in ('resource_id', 'deployment_id', 'action', 'requested_by', 'requested_at', 'approval_state',
                   'job_id', 'status', 'correlation_id', 'request_id', 'retry_of'):
        op.create_index(op.f('ix_day2_action_requests_' + column), 'day2_action_requests', [column])

    op.create_table(
        'day2_resource_locks',
        sa.Column('resource_id', sa.String(length=36), nullable=False),
        sa.Column('action_request_id', sa.String(length=36), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['action_request_id'], ['day2_action_requests.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('resource_id'),
        sa.UniqueConstraint('action_request_id'),
    )
    op.create_index(op.f('ix_day2_resource_locks_action_request_id'), 'day2_resource_locks', ['action_request_id'])
    op.create_index(op.f('ix_day2_resource_locks_expires_at'), 'day2_resource_locks', ['expires_at'])

    op.create_table(
        'day2_resource_states',
        sa.Column('resource_id', sa.String(length=36), nullable=False),
        sa.Column('protected', sa.Boolean(), nullable=False),
        sa.Column('platform_metadata', sa.JSON(), nullable=False),
        sa.Column('provider_metadata', sa.JSON(), nullable=False),
        sa.Column('desired_configuration', sa.JSON(), nullable=False),
        sa.Column('actual_configuration', sa.JSON(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('resource_id'),
    )
    op.create_index(op.f('ix_day2_resource_states_protected'), 'day2_resource_states', ['protected'])

    op.create_table(
        'bulk_day2_action_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('resource_ids', sa.JSON(), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('request_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('action', 'requested_by', 'status', 'request_id'):
        op.create_index(op.f('ix_bulk_day2_action_requests_' + column), 'bulk_day2_action_requests', [column])


def downgrade():
    for column in ('request_id', 'status', 'requested_by', 'action'):
        op.drop_index(op.f('ix_bulk_day2_action_requests_' + column), table_name='bulk_day2_action_requests')
    op.drop_table('bulk_day2_action_requests')
    op.drop_index(op.f('ix_day2_resource_states_protected'), table_name='day2_resource_states')
    op.drop_table('day2_resource_states')
    op.drop_index(op.f('ix_day2_resource_locks_expires_at'), table_name='day2_resource_locks')
    op.drop_index(op.f('ix_day2_resource_locks_action_request_id'), table_name='day2_resource_locks')
    op.drop_table('day2_resource_locks')
    for column in ('retry_of', 'request_id', 'correlation_id', 'status', 'job_id', 'approval_state', 'requested_at',
                   'requested_by', 'action', 'deployment_id', 'resource_id'):
        op.drop_index(op.f('ix_day2_action_requests_' + column), table_name='day2_action_requests')
    op.drop_table('day2_action_requests')
