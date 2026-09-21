"""add durable event broker and extension runtime"""
from alembic import op
import sqlalchemy as sa

revision = 'a8d4e6f2c913'
down_revision = 'f0a4c2d9b671'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'event_records',
        sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('type', sa.String(length=128), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=128), nullable=False),
        sa.Column('subject_type', sa.String(length=64), nullable=False),
        sa.Column('subject_id', sa.String(length=255), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('causation_id', sa.String(length=64), nullable=True),
        sa.Column('request_id', sa.String(length=36), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('token_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('sequence'),
        sa.UniqueConstraint('id'),
    )
    for column in ('id', 'type', 'subject_type', 'subject_id', 'correlation_id', 'request_id', 'created_at'):
        op.create_index(f'ix_event_records_{column}', 'event_records', [column])

    op.create_table(
        'extension_states',
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('last_event_sequence', sa.Integer(), nullable=False),
        sa.Column('failure_count', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
    op.create_index('ix_extension_states_is_enabled', 'extension_states', ['is_enabled'])
    op.create_index('ix_extension_states_status', 'extension_states', ['status'])

    op.create_table(
        'extension_deliveries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('extension_name', sa.String(length=128), nullable=False),
        sa.Column('event_sequence', sa.Integer(), nullable=False),
        sa.Column('materialization_key', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('is_replay', sa.Boolean(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['event_sequence'], ['event_records.sequence'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('materialization_key'),
    )
    for column in ('extension_name', 'event_sequence', 'materialization_key', 'status', 'is_replay', 'next_attempt_at'):
        op.create_index(f'ix_extension_deliveries_{column}', 'extension_deliveries', [column])

    with op.batch_alter_table('webhook_deliveries') as batch:
        batch.add_column(sa.Column('event_id', sa.String(length=36), nullable=True))
        batch.alter_column(
            'event',
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
        batch.alter_column(
            'resource_id',
            existing_type=sa.String(length=100),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
        batch.create_index('ix_webhook_deliveries_event_id', ['event_id'])


def downgrade():
    with op.batch_alter_table('webhook_deliveries') as batch:
        batch.drop_index('ix_webhook_deliveries_event_id')
        batch.alter_column(
            'event',
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
        batch.alter_column(
            'resource_id',
            existing_type=sa.String(length=255),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
        batch.drop_column('event_id')

    op.drop_table('extension_deliveries')
    op.drop_table('extension_states')
    op.drop_table('event_records')
