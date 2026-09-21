"""add event schema registry and durable pull consumers"""
from alembic import op
import sqlalchemy as sa

revision = 'c4f17b8d62a1'
down_revision = 'a8d4e6f2c913'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'event_schemas',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=128), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('schema_json', sa.JSON(), nullable=False),
        sa.Column('compatibility', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_type', 'version'),
    )
    op.create_index('ix_event_schemas_event_type', 'event_schemas', ['event_type'])
    op.create_index('ix_event_schemas_is_active', 'event_schemas', ['is_active'])

    op.create_table(
        'event_consumers',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('event_patterns', sa.JSON(), nullable=False),
        sa.Column(
            'cursor_sequence',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=False,
        ),
        sa.Column(
            'last_checkpoint_sequence',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=True,
        ),
        sa.Column('max_batch', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('last_polled_at', sa.DateTime(), nullable=True),
        sa.Column('last_acked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_event_consumers_cursor_sequence', 'event_consumers', ['cursor_sequence'])
    op.create_index('ix_event_consumers_is_active', 'event_consumers', ['is_active'])
    op.create_index('ix_event_consumers_owner_user_id', 'event_consumers', ['owner_user_id'])


def downgrade():
    op.drop_table('event_consumers')
    op.drop_table('event_schemas')
