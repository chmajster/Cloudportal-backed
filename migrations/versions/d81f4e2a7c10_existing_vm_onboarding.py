"""Add production brownfield Existing VM Onboarding subsystem.

Revision ID: d81f4e2a7c10
Revises: c13d9a42b5e7
"""
from alembic import op
import sqlalchemy as sa

revision = 'd81f4e2a7c10'
down_revision = 'c13d9a42b5e7'
branch_labels = None
depends_on = None


def _scope_columns():
    return (
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
    )


def _scope_constraints(table):
    return (
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name=f'fk_{table}_project_scope', ondelete='RESTRICT',
        ),
        sa.UniqueConstraint('tenant_id', 'project_id', 'id', name=f'uq_{table}_scoped_id'),
    )


def _scope_index(table):
    op.create_index(f'ix_{table}_project_scope', table, ['tenant_id', 'project_id'])


def upgrade():
    with op.batch_alter_table('managed_vms') as batch:
        batch.alter_column('management_mode', type_=sa.String(length=24), existing_type=sa.String(length=16))
        batch.add_column(sa.Column('management_source', sa.String(length=24), nullable=False, server_default='external'))
        batch.add_column(sa.Column('provisioning_source', sa.String(length=24), nullable=False, server_default='unknown'))
        batch.add_column(sa.Column('metadata_json', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.create_index('ix_managed_vms_management_source', ['management_source'])
        batch.create_index('ix_managed_vms_provisioning_source', ['provisioning_source'])

    op.execute(sa.text(
        "UPDATE managed_vms SET "
        "management_source = CASE "
        "WHEN management_mode IN ('terraform','proxmox') THEN 'cloudportal' "
        "ELSE 'external' END, "
        "provisioning_source = CASE "
        "WHEN management_mode = 'terraform' THEN 'terraform' "
        "WHEN management_mode = 'proxmox' THEN 'manual' "
        "ELSE 'external' END"
    ))

    with op.batch_alter_table('managed_resources') as batch:
        batch.alter_column('deployment_id', nullable=True, existing_type=sa.String(length=36))
        batch.add_column(sa.Column('management_source', sa.String(length=24), nullable=False, server_default='cloudportal'))
        batch.add_column(sa.Column('provisioning_source', sa.String(length=24), nullable=False, server_default='unknown'))
        batch.create_index('ix_managed_resources_management_source', ['management_source'])
        batch.create_index('ix_managed_resources_provisioning_source', ['provisioning_source'])

    op.create_table(
        'onboarding_discovery_sessions',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('scope_json', sa.JSON(), nullable=False),
        sa.Column('filters_json', sa.JSON(), nullable=False),
        sa.Column('discovered_count', sa.Integer(), nullable=False),
        sa.Column('error_summary', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
        *_scope_constraints('onboarding_discovery_sessions'),
        sa.UniqueConstraint('job_id'),
    )
    _scope_index('onboarding_discovery_sessions')
    op.create_index('ix_onboarding_discovery_sessions_provider_id', 'onboarding_discovery_sessions', ['provider_id'])
    op.create_index('ix_onboarding_discovery_sessions_status', 'onboarding_discovery_sessions', ['status'])

    op.create_table(
        'onboarding_discovered_resources',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('cluster_id', sa.String(length=255), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('resource_type', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('provider_uuid', sa.String(length=255), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('power_state', sa.String(length=32), nullable=False),
        sa.Column('is_template', sa.Boolean(), nullable=False),
        sa.Column('discovery_status', sa.String(length=24), nullable=False),
        sa.Column('normalized_json', sa.JSON(), nullable=False),
        sa.Column('raw_metadata_json', sa.JSON(), nullable=False),
        sa.Column('match_candidates_json', sa.JSON(), nullable=False),
        sa.Column('rule_trace_json', sa.JSON(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['onboarding_discovery_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint(
            'session_id', 'provider_id', 'cluster_id', 'resource_type', 'external_id',
            name='uq_onboarding_discovered_identity',
        ),
    )
    op.create_index('ix_onboarding_discovered_resources_session_id', 'onboarding_discovered_resources', ['session_id'])
    op.create_index('ix_onboarding_discovered_resources_provider_id', 'onboarding_discovered_resources', ['provider_id'])
    op.create_index('ix_onboarding_discovered_resources_provider_uuid', 'onboarding_discovered_resources', ['provider_uuid'])
    op.create_index('ix_onboarding_discovered_resources_hostname', 'onboarding_discovered_resources', ['hostname'])
    op.create_index('ix_onboarding_discovered_resources_last_seen_at', 'onboarding_discovered_resources', ['last_seen_at'])
    op.create_index('ix_onboarding_discovered_status', 'onboarding_discovered_resources', ['session_id', 'discovery_status'])

    op.create_table(
        'onboarding_rules',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('stop_processing', sa.Boolean(), nullable=False),
        sa.Column('conditions_json', sa.JSON(), nullable=False),
        sa.Column('effects_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
        *_scope_constraints('onboarding_rules'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'name', name='uq_onboarding_rule_name_scope'),
    )
    _scope_index('onboarding_rules')
    op.create_index(
        'ix_onboarding_rules_order', 'onboarding_rules',
        ['tenant_id', 'project_id', 'enabled', 'priority'],
    )

    op.create_table(
        'resource_external_identities',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('managed_vm_id', sa.String(length=36), nullable=True),
        sa.Column('managed_resource_id', sa.String(length=36), nullable=True),
        sa.Column('resource_type', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('cluster_id', sa.String(length=255), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('provider_uuid', sa.String(length=255), nullable=True),
        sa.Column('management_mode', sa.String(length=24), nullable=False),
        sa.Column('management_source', sa.String(length=24), nullable=False),
        sa.Column('provisioning_source', sa.String(length=24), nullable=False),
        sa.Column('guest_credential_id', sa.Integer(), nullable=True),
        sa.Column('awx_credential_id', sa.Integer(), nullable=True),
        sa.Column('business_metadata_json', sa.JSON(), nullable=False),
        sa.Column('provider_metadata_json', sa.JSON(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('onboarded_at', sa.DateTime(), nullable=True),
        sa.Column('onboarded_by', sa.Integer(), nullable=True),
        sa.Column('retired_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'managed_vm_id'],
            ['managed_vms.tenant_id', 'managed_vms.project_id', 'managed_vms.id'],
            name='fk_resource_external_identity_vm_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'managed_resource_id'],
            ['managed_resources.tenant_id', 'managed_resources.project_id', 'managed_resources.id'],
            name='fk_resource_external_identity_resource_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['guest_credential_id'], ['credentials.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['awx_credential_id'], ['credentials.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['onboarded_by'], ['users.id'], ondelete='SET NULL'),
        *_scope_constraints('resource_external_identities'),
        sa.UniqueConstraint(
            'provider_id', 'cluster_id', 'resource_type', 'external_id',
            name='uq_resource_external_identity',
        ),
        sa.CheckConstraint(
            '(managed_vm_id IS NOT NULL AND managed_resource_id IS NULL) OR '
            '(managed_vm_id IS NULL AND managed_resource_id IS NOT NULL)',
            name='ck_resource_external_identity_one_target',
        ),
    )
    _scope_index('resource_external_identities')
    op.create_index('ix_resource_external_identities_provider_id', 'resource_external_identities', ['provider_id'])
    op.create_index('ix_resource_external_identity_vm', 'resource_external_identities', ['managed_vm_id'])
    op.create_index('ix_resource_external_identity_resource', 'resource_external_identities', ['managed_resource_id'])
    op.create_index('ix_resource_external_identities_provider_uuid', 'resource_external_identities', ['provider_uuid'])
    op.create_index('ix_resource_external_identities_last_seen_at', 'resource_external_identities', ['last_seen_at'])

    op.create_table(
        'resource_sync_states',
        sa.Column('identity_id', sa.String(length=36), primary_key=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('expected_json', sa.JSON(), nullable=False),
        sa.Column('actual_json', sa.JSON(), nullable=False),
        sa.Column('drift_json', sa.JSON(), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('next_sync_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['identity_id'], ['resource_external_identities.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_resource_sync_states_status', 'resource_sync_states', ['status'])
    op.create_index('ix_resource_sync_states_last_sync_at', 'resource_sync_states', ['last_sync_at'])
    op.create_index('ix_resource_sync_states_last_seen_at', 'resource_sync_states', ['last_seen_at'])
    op.create_index('ix_resource_sync_states_next_sync_at', 'resource_sync_states', ['next_sync_at'])

    op.create_table(
        'onboarding_conflicts',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('discovered_resource_id', sa.String(length=36), nullable=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('conflict_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('candidates_json', sa.JSON(), nullable=False),
        sa.Column('details_json', sa.JSON(), nullable=False),
        sa.Column('resolution', sa.String(length=32), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(['discovered_resource_id'], ['onboarding_discovered_resources.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
        *_scope_constraints('onboarding_conflicts'),
    )
    _scope_index('onboarding_conflicts')
    op.create_index('ix_onboarding_conflicts_discovered_resource_id', 'onboarding_conflicts', ['discovered_resource_id'])
    op.create_index('ix_onboarding_conflicts_provider_id', 'onboarding_conflicts', ['provider_id'])
    op.create_index(
        'ix_onboarding_conflict_scope_status', 'onboarding_conflicts',
        ['tenant_id', 'project_id', 'status'],
    )

    op.create_table(
        'onboarding_job_items',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('discovered_resource_id', sa.String(length=36), nullable=False),
        sa.Column('external_identity_id', sa.String(length=36), nullable=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('stage', sa.String(length=64), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['discovered_resource_id'], ['onboarding_discovered_resources.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['external_identity_id'], ['resource_external_identities.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('job_id', 'discovered_resource_id', name='uq_onboarding_job_resource'),
    )
    op.create_index('ix_onboarding_job_items_job_id', 'onboarding_job_items', ['job_id'])
    op.create_index('ix_onboarding_job_items_discovered_resource_id', 'onboarding_job_items', ['discovered_resource_id'])
    op.create_index('ix_onboarding_job_items_status', 'onboarding_job_items', ['job_id', 'status'])

    op.create_table(
        'onboarding_schedules',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('discovery_scope_json', sa.JSON(), nullable=False),
        sa.Column('filters_json', sa.JSON(), nullable=False),
        sa.Column('interval_seconds', sa.Integer(), nullable=False),
        sa.Column('next_run_at', sa.DateTime(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('auto_classification', sa.Boolean(), nullable=False),
        sa.Column('automatic_inventory_import', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *_scope_columns(),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
        *_scope_constraints('onboarding_schedules'),
    )
    _scope_index('onboarding_schedules')
    op.create_index('ix_onboarding_schedules_provider_id', 'onboarding_schedules', ['provider_id'])
    op.create_index(
        'ix_onboarding_schedules_due', 'onboarding_schedules',
        ['is_active', 'next_run_at'],
    )


def downgrade():
    op.drop_table('onboarding_schedules')
    op.drop_table('onboarding_job_items')
    op.drop_table('onboarding_conflicts')
    op.drop_table('resource_sync_states')
    op.drop_table('resource_external_identities')
    op.drop_table('onboarding_rules')
    op.drop_table('onboarding_discovered_resources')
    op.drop_table('onboarding_discovery_sessions')

    # Generic resources without a deployment can only have been created by this
    # revision. The legacy schema has no way to represent them.
    op.execute(sa.text("DELETE FROM managed_resources WHERE deployment_id IS NULL"))
    with op.batch_alter_table('managed_resources') as batch:
        batch.drop_index('ix_managed_resources_provisioning_source')
        batch.drop_index('ix_managed_resources_management_source')
        batch.drop_column('provisioning_source')
        batch.drop_column('management_source')
        batch.alter_column('deployment_id', nullable=False, existing_type=sa.String(length=36))

    with op.batch_alter_table('managed_vms') as batch:
        batch.drop_index('ix_managed_vms_provisioning_source')
        batch.drop_index('ix_managed_vms_management_source')
        batch.drop_column('metadata_json')
        batch.drop_column('provisioning_source')
        batch.drop_column('management_source')
        batch.alter_column('management_mode', type_=sa.String(length=16), existing_type=sa.String(length=24))
