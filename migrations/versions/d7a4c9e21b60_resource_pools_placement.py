"""Add Resource Pools, placement decisions and logical mappings.

Revision ID: d7a4c9e21b60
Revises: c13d9a42b5e7
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7a4c9e21b60'
down_revision = 'c13d9a42b5e7'
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def _upgrade_existing_blueprints():
    bind = op.get_bind()
    metadata = sa.MetaData()
    blueprints = sa.Table('blueprints', metadata, autoload_with=bind)
    rows = bind.execute(sa.select(blueprints.c.id, blueprints.c.deployment)).all()
    for blueprint_id, deployment in rows:
        if not isinstance(deployment, dict):
            continue
        if deployment.get('placement_mode'):
            continue
        updated = dict(deployment)
        updated['placement_mode'] = 'FIXED'
        updated.setdefault('resource_pool_id', None)
        updated.setdefault('logical_network', None)
        updated.setdefault('storage_class', None)
        bind.execute(
            blueprints.update().where(blueprints.c.id == blueprint_id).values(deployment=updated)
        )


def _downgrade_existing_blueprints():
    bind = op.get_bind()
    metadata = sa.MetaData()
    blueprints = sa.Table('blueprints', metadata, autoload_with=bind)
    rows = bind.execute(sa.select(blueprints.c.id, blueprints.c.deployment)).all()
    for blueprint_id, deployment in rows:
        if not isinstance(deployment, dict):
            continue
        updated = dict(deployment)
        updated.pop('placement_mode', None)
        updated.pop('resource_pool_id', None)
        updated.pop('logical_network', None)
        updated.pop('storage_class', None)
        updated.pop('affinity', None)
        bind.execute(
            blueprints.update().where(blueprints.c.id == blueprint_id).values(deployment=updated)
        )


def upgrade():
    op.create_table(
        'resource_pools',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('normalized_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('strategy', sa.String(length=32), nullable=False, server_default='BALANCED'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('thresholds', sa.JSON(), nullable=False),
        sa.Column('retry_limit', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('reservation_ttl_seconds', sa.Integer(), nullable=False, server_default='600'),
        sa.Column('round_robin_cursor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('placement_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_resource_pools_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_resource_pools_scope_name'),
    )
    op.create_index('ix_resource_pools_scope', 'resource_pools', ['tenant_id', 'project_id'])
    op.create_index('ix_resource_pools_enabled', 'resource_pools', ['enabled'])

    op.create_table(
        'resource_pool_members',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('pool_id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('platform_id', sa.String(length=100), nullable=True),
        sa.Column('provider_type', sa.String(length=32), nullable=False),
        sa.Column('cluster', sa.String(length=100), nullable=True),
        sa.Column('node', sa.String(length=100), nullable=True),
        sa.Column('datacenter', sa.String(length=100), nullable=True),
        sa.Column('storage', sa.String(length=100), nullable=True),
        sa.Column('network', sa.String(length=100), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('weight', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('maintenance_mode', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('max_vm_count', sa.Integer(), nullable=True),
        sa.Column('max_cpu_usage', sa.Float(), nullable=True),
        sa.Column('max_memory_usage', sa.Float(), nullable=True),
        sa.Column('min_free_memory_mb', sa.Integer(), nullable=True),
        sa.Column('min_free_storage_gb', sa.Integer(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['pool_id'], ['resource_pools.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_resource_pool_members_pool', 'resource_pool_members', ['pool_id'])
    op.create_index('ix_resource_pool_members_provider', 'resource_pool_members', ['provider_id'])
    op.create_index('ix_resource_pool_members_enabled', 'resource_pool_members', ['pool_id', 'enabled', 'maintenance_mode'])

    op.create_table(
        'placement_rules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('pool_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('normalized_name', sa.String(length=100), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('conditions', sa.JSON(), nullable=False),
        sa.Column('actions', sa.JSON(), nullable=False),
        sa.Column('stop_processing', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['pool_id'], ['resource_pools.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pool_id', 'normalized_name', name='uq_placement_rules_pool_name'),
    )
    op.create_index('ix_placement_rules_pool_priority', 'placement_rules', ['pool_id', 'enabled', 'priority'])

    op.create_table(
        'logical_networks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('normalized_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_logical_networks_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_logical_networks_scope_name'),
    )
    op.create_index('ix_logical_networks_scope', 'logical_networks', ['tenant_id', 'project_id'])

    op.create_table(
        'network_mappings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('logical_network_id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('node', sa.String(length=100), nullable=True),
        sa.Column('network', sa.String(length=100), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['logical_network_id'], ['logical_networks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('logical_network_id', 'provider_id', 'node', name='uq_network_mapping_target'),
    )
    op.create_index('ix_network_mappings_logical', 'network_mappings', ['logical_network_id'])

    op.create_table(
        'storage_classes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('normalized_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_storage_classes_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_storage_classes_scope_name'),
    )
    op.create_index('ix_storage_classes_scope', 'storage_classes', ['tenant_id', 'project_id'])

    op.create_table(
        'storage_mappings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('storage_class_id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('node', sa.String(length=100), nullable=True),
        sa.Column('storage', sa.String(length=100), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['storage_class_id'], ['storage_classes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_class_id', 'provider_id', 'node', name='uq_storage_mapping_target'),
    )
    op.create_index('ix_storage_mappings_class', 'storage_mappings', ['storage_class_id'])

    op.create_table(
        'placement_decisions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=True),
        sa.Column('vm_id', sa.String(length=100), nullable=True),
        sa.Column('blueprint_id', sa.Integer(), nullable=True),
        sa.Column('pool_id', sa.String(length=36), nullable=True),
        sa.Column('rule_ids', sa.JSON(), nullable=False),
        sa.Column('placement_mode', sa.String(length=16), nullable=False, server_default='FIXED'),
        sa.Column('selected_provider', sa.String(length=32), nullable=False),
        sa.Column('selected_provider_id', sa.Integer(), nullable=False),
        sa.Column('selected_platform', sa.String(length=100), nullable=False),
        sa.Column('selected_member_id', sa.String(length=36), nullable=True),
        sa.Column('selected_node', sa.String(length=100), nullable=True),
        sa.Column('selected_storage', sa.String(length=100), nullable=True),
        sa.Column('selected_network', sa.String(length=100), nullable=True),
        sa.Column('score', sa.Float(), nullable=False, server_default='0'),
        sa.Column('candidates_snapshot', sa.JSON(), nullable=False),
        sa.Column('decision_reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('request_snapshot', sa.JSON(), nullable=False),
        sa.Column('is_override', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_placement_decisions_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['blueprint_id'], ['blueprints.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['pool_id'], ['resource_pools.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['selected_provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['selected_member_id'], ['resource_pool_members.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_placement_decisions_scope', 'placement_decisions', ['tenant_id', 'project_id'])
    op.create_index('ix_placement_decisions_deployment', 'placement_decisions', ['deployment_id'])
    op.create_index('ix_placement_decisions_pool', 'placement_decisions', ['pool_id'])

    op.create_table(
        'resource_reservations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('pool_id', sa.String(length=36), nullable=False),
        sa.Column('member_id', sa.String(length=36), nullable=False),
        sa.Column('decision_id', sa.String(length=36), nullable=True),
        sa.Column('deployment_id', sa.String(length=36), nullable=True),
        sa.Column('job_id', sa.String(length=36), nullable=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('node', sa.String(length=100), nullable=True),
        sa.Column('storage', sa.String(length=100), nullable=True),
        sa.Column('cpu', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('memory_mb', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('storage_gb', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='RESERVED'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['pool_id'], ['resource_pools.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['member_id'], ['resource_pool_members.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decision_id'], ['placement_decisions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_resource_reservations_pool_status', 'resource_reservations', ['pool_id', 'status', 'expires_at'])
    op.create_index('ix_resource_reservations_target', 'resource_reservations', ['provider_id', 'node', 'storage', 'status'])
    op.create_index('ix_resource_reservations_deployment', 'resource_reservations', ['deployment_id'])
    op.create_index('ix_resource_reservations_status', 'resource_reservations', ['status'])
    op.create_index('ix_resource_reservations_expires_at', 'resource_reservations', ['expires_at'])

    _upgrade_existing_blueprints()


def downgrade():
    _downgrade_existing_blueprints()

    op.drop_index('ix_resource_reservations_expires_at', table_name='resource_reservations')
    op.drop_index('ix_resource_reservations_status', table_name='resource_reservations')
    op.drop_index('ix_resource_reservations_deployment', table_name='resource_reservations')
    op.drop_index('ix_resource_reservations_target', table_name='resource_reservations')
    op.drop_index('ix_resource_reservations_pool_status', table_name='resource_reservations')
    op.drop_table('resource_reservations')

    op.drop_index('ix_placement_decisions_pool', table_name='placement_decisions')
    op.drop_index('ix_placement_decisions_deployment', table_name='placement_decisions')
    op.drop_index('ix_placement_decisions_scope', table_name='placement_decisions')
    op.drop_table('placement_decisions')

    op.drop_index('ix_storage_mappings_class', table_name='storage_mappings')
    op.drop_table('storage_mappings')
    op.drop_index('ix_storage_classes_scope', table_name='storage_classes')
    op.drop_table('storage_classes')

    op.drop_index('ix_network_mappings_logical', table_name='network_mappings')
    op.drop_table('network_mappings')
    op.drop_index('ix_logical_networks_scope', table_name='logical_networks')
    op.drop_table('logical_networks')

    op.drop_index('ix_placement_rules_pool_priority', table_name='placement_rules')
    op.drop_table('placement_rules')
    op.drop_index('ix_resource_pool_members_enabled', table_name='resource_pool_members')
    op.drop_index('ix_resource_pool_members_provider', table_name='resource_pool_members')
    op.drop_index('ix_resource_pool_members_pool', table_name='resource_pool_members')
    op.drop_table('resource_pool_members')
    op.drop_index('ix_resource_pools_enabled', table_name='resource_pools')
    op.drop_index('ix_resource_pools_scope', table_name='resource_pools')
    op.drop_table('resource_pools')
