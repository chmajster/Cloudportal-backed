from alembic import op
import sqlalchemy as sa


revision = 'f9d6c2a81e44'
down_revision = 'e91c6b2a4d73'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'policy_definitions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('policy_type', sa.String(length=40), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5000'),
        sa.Column('enforcement', sa.String(length=16), nullable=False, server_default='hard'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='draft'),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('scope', sa.JSON(), nullable=False),
        sa.Column('condition', sa.JSON(), nullable=False),
        sa.Column('effects', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for name, columns in (
        ('ix_policy_definitions_name', ['name']),
        ('ix_policy_definitions_policy_type', ['policy_type']),
        ('ix_policy_definitions_priority', ['priority']),
        ('ix_policy_definitions_enforcement', ['enforcement']),
        ('ix_policy_definitions_status', ['status']),
        ('ix_policy_definitions_tenant_id', ['tenant_id']),
        ('ix_policy_definitions_project_id', ['project_id']),
        ('ix_policy_definitions_created_by', ['created_by']),
        ('ix_policy_definitions_updated_by', ['updated_by']),
    ):
        op.create_index(name, 'policy_definitions', columns, unique=False)

    op.create_table(
        'policy_versions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('policy_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['policy_id'], ['policy_definitions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('policy_id', 'version', name='uq_policy_version'),
    )
    op.create_index('ix_policy_versions_policy_id', 'policy_versions', ['policy_id'], unique=False)
    op.create_index('ix_policy_versions_created_by', 'policy_versions', ['created_by'], unique=False)
    op.create_index('ix_policy_versions_created_at', 'policy_versions', ['created_at'], unique=False)

    op.create_table(
        'policy_exceptions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('policy_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('ticket', sa.String(length=160), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='approved'),
        sa.Column('condition', sa.JSON(), nullable=False),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['policy_id'], ['policy_definitions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for name, columns in (
        ('ix_policy_exceptions_policy_id', ['policy_id']),
        ('ix_policy_exceptions_status', ['status']),
        ('ix_policy_exceptions_valid_from', ['valid_from']),
        ('ix_policy_exceptions_valid_until', ['valid_until']),
        ('ix_policy_exceptions_created_by', ['created_by']),
        ('ix_policy_exceptions_approved_by', ['approved_by']),
    ):
        op.create_index(name, 'policy_exceptions', columns, unique=False)

    op.create_table(
        'policy_decisions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('resource_type', sa.String(length=64), nullable=False),
        sa.Column('resource_id', sa.String(length=160), nullable=True),
        sa.Column('decision', sa.String(length=24), nullable=False),
        sa.Column('matched_policy_ids', sa.JSON(), nullable=False),
        sa.Column('matched_policy_versions', sa.JSON(), nullable=False),
        sa.Column('effects', sa.JSON(), nullable=False),
        sa.Column('trace', sa.JSON(), nullable=False),
        sa.Column('context_summary', sa.JSON(), nullable=False),
        sa.Column('dry_run', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint('id'),
    )
    for name, columns in (
        ('ix_policy_decisions_timestamp', ['timestamp']),
        ('ix_policy_decisions_tenant_id', ['tenant_id']),
        ('ix_policy_decisions_project_id', ['project_id']),
        ('ix_policy_decisions_actor_user_id', ['actor_user_id']),
        ('ix_policy_decisions_action', ['action']),
        ('ix_policy_decisions_resource_type', ['resource_type']),
        ('ix_policy_decisions_resource_id', ['resource_id']),
        ('ix_policy_decisions_decision', ['decision']),
        ('ix_policy_decisions_dry_run', ['dry_run']),
    ):
        op.create_index(name, 'policy_decisions', columns, unique=False)


def downgrade():
    op.drop_table('policy_decisions')
    op.drop_table('policy_exceptions')
    op.drop_table('policy_versions')
    op.drop_table('policy_definitions')
