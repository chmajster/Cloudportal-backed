"""Add hierarchical quota limits, usage ledger and atomic reservations.

Revision ID: d3f8c41b72a0
Revises: c864db917f20
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3f8c41b72a0'
down_revision = 'c864db917f20'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tenant_quota_limits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dimension', sa.String(32), nullable=False),
        sa.Column('limit_value', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('tenant_id', 'dimension', name='uq_tenant_quota_limit_dimension'),
    )
    op.create_index('ix_tenant_quota_limits_tenant_id', 'tenant_quota_limits', ['tenant_id'])
    op.create_index('ix_tenant_quota_limits_dimension', 'tenant_quota_limits', ['dimension'])
    op.create_table(
        'project_quota_limits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('dimension', sa.String(32), nullable=False),
        sa.Column('limit_value', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                name='fk_project_quota_limit_scope', ondelete='CASCADE'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'dimension', name='uq_project_quota_limit_dimension'),
    )
    op.create_index('ix_project_quota_limit_scope', 'project_quota_limits', ['tenant_id', 'project_id'])
    op.create_index('ix_project_quota_limits_dimension', 'project_quota_limits', ['dimension'])
    op.create_table(
        'tenant_quota_usage',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dimension', sa.String(32), nullable=False),
        sa.Column('used', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('reserved', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('tenant_id', 'dimension', name='uq_tenant_quota_usage_dimension'),
    )
    op.create_index('ix_tenant_quota_usage_tenant_id', 'tenant_quota_usage', ['tenant_id'])
    op.create_index('ix_tenant_quota_usage_dimension', 'tenant_quota_usage', ['dimension'])
    op.create_table(
        'project_quota_usage',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('dimension', sa.String(32), nullable=False),
        sa.Column('used', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('reserved', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                name='fk_project_quota_usage_scope', ondelete='CASCADE'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'dimension', name='uq_project_quota_usage_dimension'),
    )
    op.create_index('ix_project_quota_usage_scope', 'project_quota_usage', ['tenant_id', 'project_id'])
    op.create_index('ix_project_quota_usage_dimension', 'project_quota_usage', ['dimension'])
    op.create_table(
        'quota_reservations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('request_key', sa.String(128), nullable=False),
        sa.Column('subject_type', sa.String(32), nullable=False),
        sa.Column('subject_id', sa.String(128), nullable=False),
        sa.Column('operation', sa.String(64), nullable=False),
        sa.Column('deltas', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('reconciliation_required', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('committed_at', sa.DateTime(), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('reconciled_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                name='fk_quota_reservation_scope', ondelete='RESTRICT'),
        sa.UniqueConstraint('request_key', name='uq_quota_reservation_request_key'),
    )
    op.create_index('ix_quota_reservations_request_key', 'quota_reservations', ['request_key'])
    op.create_index('ix_quota_reservations_status', 'quota_reservations', ['status'])
    op.create_index('ix_quota_reservation_scope_status', 'quota_reservations', ['tenant_id', 'project_id', 'status'])
    op.create_index('ix_quota_reservation_subject', 'quota_reservations',
                    ['tenant_id', 'project_id', 'subject_type', 'subject_id'])
    op.create_table(
        'quota_allocations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('subject_type', sa.String(32), nullable=False),
        sa.Column('subject_id', sa.String(128), nullable=False),
        sa.Column('dimensions', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                name='fk_quota_allocation_scope', ondelete='RESTRICT'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'subject_type', 'subject_id',
                            name='uq_quota_allocation_subject'),
    )
    op.create_index('ix_quota_allocation_scope', 'quota_allocations', ['tenant_id', 'project_id'])
    op.create_table(
        'quota_ledger_entries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('reservation_id', sa.String(36), sa.ForeignKey('quota_reservations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event', sa.String(32), nullable=False),
        sa.Column('subject_type', sa.String(32), nullable=False),
        sa.Column('subject_id', sa.String(128), nullable=False),
        sa.Column('deltas', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                name='fk_quota_ledger_scope', ondelete='RESTRICT'),
    )
    op.create_index('ix_quota_ledger_entries_reservation_id', 'quota_ledger_entries', ['reservation_id'])
    op.create_index('ix_quota_ledger_entries_event', 'quota_ledger_entries', ['event'])
    op.create_index('ix_quota_ledger_scope_created', 'quota_ledger_entries', ['tenant_id', 'project_id', 'created_at'])
    _backfill_confirmed_deployments()


def _backfill_confirmed_deployments():
    import uuid
    from datetime import datetime, timezone

    connection = op.get_bind()
    deployments = sa.table(
        'deployments',
        sa.column('id'), sa.column('tenant_id'), sa.column('project_id'),
        sa.column('provider'), sa.column('variables', sa.JSON()), sa.column('status'), sa.column('destroyed_at'),
    )
    managed_vms = sa.table(
        'managed_vms',
        sa.column('deployment_id'), sa.column('lifecycle_status'), sa.column('destroyed_at'),
    )
    managed_resources = sa.table(
        'managed_resources',
        sa.column('deployment_id'), sa.column('lifecycle_status'), sa.column('destroyed_at'),
    )
    allocation = sa.table(
        'quota_allocations',
        sa.column('id'), sa.column('tenant_id'), sa.column('project_id'),
        sa.column('subject_type'), sa.column('subject_id'), sa.column('dimensions', sa.JSON()),
        sa.column('created_at'), sa.column('updated_at'),
    )
    tenant_usage = sa.table(
        'tenant_quota_usage',
        sa.column('id'), sa.column('tenant_id'), sa.column('dimension'),
        sa.column('used'), sa.column('reserved'), sa.column('created_at'), sa.column('updated_at'),
    )
    project_usage = sa.table(
        'project_quota_usage',
        sa.column('id'), sa.column('tenant_id'), sa.column('project_id'), sa.column('dimension'),
        sa.column('used'), sa.column('reserved'), sa.column('created_at'), sa.column('updated_at'),
    )
    active_vm = sa.exists().where(
        managed_vms.c.deployment_id == deployments.c.id,
        managed_vms.c.lifecycle_status == 'active',
        managed_vms.c.destroyed_at.is_(None),
    )
    active_resource = sa.exists().where(
        managed_resources.c.deployment_id == deployments.c.id,
        managed_resources.c.lifecycle_status == 'active',
        managed_resources.c.destroyed_at.is_(None),
    )
    rows = connection.execute(sa.select(deployments).where(
        deployments.c.destroyed_at.is_(None),
        sa.or_(
            deployments.c.status.in_(('successful', 'imported')),
            active_vm,
            active_resource,
        ),
    )).mappings().all()
    tenant_totals = {}
    project_totals = {}
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        variables = dict(row['variables'] or {})
        provider = str(row['provider'] or '').lower()
        dimensions = {'vm_count': 1}

        def positive_int(value):
            if value is None or isinstance(value, bool):
                return None
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return None

        if provider in {'proxmox', 'vmware'}:
            cpu = positive_int(variables.get('cpu'))
            memory = positive_int(variables.get('memory'))
            disk = positive_int(variables.get('disk'))
            if cpu:
                dimensions['vcpu'] = cpu
            if memory:
                dimensions['memory_mb'] = memory
            if disk:
                dimensions['disk_gib'] = disk
        elif provider == 'aws':
            disk = positive_int(variables.get('root_volume_size'))
            if disk:
                dimensions['disk_gib'] = disk
        elif provider == 'azure':
            disk = positive_int(variables.get('os_disk_size_gb'))
            if disk:
                dimensions['disk_gib'] = disk
        connection.execute(allocation.insert().values(
            id=str(uuid.uuid4()), tenant_id=row['tenant_id'], project_id=row['project_id'],
            subject_type='deployment', subject_id=row['id'], dimensions=dimensions,
            created_at=stamp, updated_at=stamp,
        ))
        for dimension, value in dimensions.items():
            tenant_totals[(row['tenant_id'], dimension)] = tenant_totals.get((row['tenant_id'], dimension), 0) + value
            key = (row['tenant_id'], row['project_id'], dimension)
            project_totals[key] = project_totals.get(key, 0) + value
    for (tenant_id, dimension), value in tenant_totals.items():
        connection.execute(tenant_usage.insert().values(
            id=str(uuid.uuid4()), tenant_id=tenant_id, dimension=dimension,
            used=value, reserved=0, created_at=stamp, updated_at=stamp,
        ))
    for (tenant_id, project_id, dimension), value in project_totals.items():
        connection.execute(project_usage.insert().values(
            id=str(uuid.uuid4()), tenant_id=tenant_id, project_id=project_id, dimension=dimension,
            used=value, reserved=0, created_at=stamp, updated_at=stamp,
        ))


def downgrade():
    connection = op.get_bind()
    limits = sa.table('tenant_quota_limits', sa.column('id'))
    project_limits = sa.table('project_quota_limits', sa.column('id'))
    reservations = sa.table('quota_reservations', sa.column('status'))
    if connection.scalar(sa.select(sa.func.count()).select_from(limits)):
        raise RuntimeError('Cannot downgrade quota accounting while tenant limits exist')
    if connection.scalar(sa.select(sa.func.count()).select_from(project_limits)):
        raise RuntimeError('Cannot downgrade quota accounting while project limits exist')
    if connection.scalar(sa.select(sa.func.count()).select_from(reservations).where(
            reservations.c.status.in_(('reserved', 'uncertain')))):
        raise RuntimeError('Cannot downgrade quota accounting with active or uncertain reservations')
    op.drop_table('quota_ledger_entries')
    op.drop_table('quota_allocations')
    op.drop_table('quota_reservations')
    op.drop_table('project_quota_usage')
    op.drop_table('tenant_quota_usage')
    op.drop_table('project_quota_limits')
    op.drop_table('tenant_quota_limits')
