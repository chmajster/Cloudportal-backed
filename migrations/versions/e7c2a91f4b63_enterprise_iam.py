"""Enterprise IAM / RBAC / ABAC assignment layer.

Revision ID: e7c2a91f4b63
Revises: c13d9a42b5e7
"""
from datetime import datetime
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'e7c2a91f4b63'
down_revision = 'c13d9a42b5e7'
branch_labels = None
depends_on = None


SYSTEM_ROLE_NAMES = {
    'Administrator', 'Global Administrator', 'Infrastructure Administrator',
    'Operator', 'Viewer', 'Read Only', 'Auditor', 'Organization Administrator',
    'Organization Auditor', 'Tenant Administrator', 'Tenant Viewer',
    'Project Administrator', 'Project Viewer', 'Project Operator', 'Developer',
    'Infrastructure Operator', 'Security Administrator', 'Approval Manager',
    'Blueprint Administrator', 'Credential Administrator', 'Self Service User',
    'Portal Service',
}

SCOPE_TYPES = [
    'GLOBAL', 'ORGANIZATION', 'PROJECT', 'APMID', 'ENVIRONMENT',
    'RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE',
]


def upgrade():
    op.create_table(
        'role_profiles',
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('scope_types', sa.JSON(), nullable=False),
        sa.Column('permission_patterns', sa.JSON(), nullable=False),
        sa.Column('inheritance_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('assignable_by', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('role_id'),
    )
    op.create_index('ix_role_profiles_is_system', 'role_profiles', ['is_system'], unique=False)
    op.create_index('ix_role_profiles_enabled', 'role_profiles', ['enabled'], unique=False)

    op.create_table(
        'iam_groups',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('external_source', sa.String(length=32), nullable=True),
        sa.Column('external_id', sa.String(length=1024), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_iam_groups_name'),
        sa.UniqueConstraint('external_source', 'external_id', name='uq_iam_groups_external'),
    )
    op.create_index('ix_iam_groups_enabled', 'iam_groups', ['enabled'], unique=False)
    op.create_index(
        'ix_iam_groups_external_source', 'iam_groups',
        ['external_source', 'enabled'], unique=False,
    )

    op.create_table(
        'iam_group_members',
        sa.Column('group_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False, server_default='LOCAL'),
        sa.Column('external_id', sa.String(length=1024), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['iam_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('group_id', 'user_id'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_iam_group_member'),
    )
    op.create_index(
        'ix_iam_group_members_user', 'iam_group_members',
        ['user_id', 'group_id'], unique=False,
    )

    op.create_table(
        'iam_role_assignments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('subject_type', sa.String(length=24), nullable=False),
        sa.Column('subject_id', sa.String(length=128), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('effect', sa.String(length=8), nullable=False, server_default='ALLOW'),
        sa.Column('scope_type', sa.String(length=32), nullable=False),
        sa.Column('scope_id', sa.String(length=160), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('apmid', sa.String(length=63), nullable=True),
        sa.Column('environment', sa.String(length=32), nullable=True),
        sa.Column('conditions', sa.JSON(), nullable=False),
        sa.Column('permission_ceiling', sa.JSON(), nullable=True),
        sa.Column('inherit', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('approval_required', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('source', sa.String(length=32), nullable=False, server_default='LOCAL'),
        sa.Column('source_ref', sa.String(length=1024), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('USER','GROUP','SERVICE_ACCOUNT','API_TOKEN')",
            name='ck_iam_assignment_subject_type',
        ),
        sa.CheckConstraint("effect IN ('ALLOW','DENY')", name='ck_iam_assignment_effect'),
        sa.CheckConstraint(
            "scope_type IN ('GLOBAL','ORGANIZATION','PROJECT','APMID','ENVIRONMENT',"
            "'RESOURCE_POOL','BLUEPRINT','DEPLOYMENT','RESOURCE','MACHINE')",
            name='ck_iam_assignment_scope_type',
        ),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for name, columns in (
        ('ix_iam_assignment_subject_active', ['subject_type', 'subject_id', 'enabled', 'valid_until']),
        ('ix_iam_assignment_scope', ['scope_type', 'scope_id', 'tenant_id', 'project_id']),
        ('ix_iam_assignment_role', ['role_id', 'enabled']),
        ('ix_iam_role_assignments_role_id', ['role_id']),
        ('ix_iam_role_assignments_tenant_id', ['tenant_id']),
        ('ix_iam_role_assignments_project_id', ['project_id']),
        ('ix_iam_role_assignments_apmid', ['apmid']),
        ('ix_iam_role_assignments_environment', ['environment']),
        ('ix_iam_role_assignments_valid_from', ['valid_from']),
        ('ix_iam_role_assignments_valid_until', ['valid_until']),
        ('ix_iam_role_assignments_enabled', ['enabled']),
        ('ix_iam_role_assignments_source', ['source']),
    ):
        op.create_index(name, 'iam_role_assignments', columns, unique=False)

    op.create_table(
        'iam_jit_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('requester_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('scope_type', sa.String(length=32), nullable=False),
        sa.Column('scope_id', sa.String(length=160), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('apmid', sa.String(length=63), nullable=True),
        sa.Column('environment', sa.String(length=32), nullable=True),
        sa.Column('requested_duration_minutes', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='PENDING'),
        sa.Column('approval_id', sa.String(length=64), nullable=True),
        sa.Column('assignment_id', sa.String(length=36), nullable=True),
        sa.Column('decided_by', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED','EXPIRED')",
            name='ck_iam_jit_status',
        ),
        sa.ForeignKeyConstraint(['requester_id'], ['users.id']),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
        sa.ForeignKeyConstraint(
            ['assignment_id'], ['iam_role_assignments.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_iam_jit_requester_status', 'iam_jit_requests',
        ['requester_id', 'status'], unique=False,
    )
    for column in ('requester_id', 'role_id', 'tenant_id', 'project_id', 'status', 'approval_id'):
        op.create_index(f'ix_iam_jit_requests_{column}', 'iam_jit_requests', [column], unique=False)

    op.create_table(
        'iam_break_glass',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('valid_from', sa.DateTime(), nullable=False),
        sa.Column('valid_until', sa.DateTime(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('ended_by', sa.Integer(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['ended_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_iam_break_glass_user_active', 'iam_break_glass',
        ['user_id', 'enabled', 'valid_until'], unique=False,
    )
    op.create_index('ix_iam_break_glass_user_id', 'iam_break_glass', ['user_id'], unique=False)
    op.create_index('ix_iam_break_glass_valid_until', 'iam_break_glass', ['valid_until'], unique=False)
    op.create_index('ix_iam_break_glass_enabled', 'iam_break_glass', ['enabled'], unique=False)

    op.create_table(
        'iam_role_conflicts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('role_a_id', sa.Integer(), nullable=False),
        sa.Column('role_b_id', sa.Integer(), nullable=False),
        sa.Column('scope_type', sa.String(length=32), nullable=False, server_default='GLOBAL'),
        sa.Column('severity', sa.String(length=16), nullable=False, server_default='BLOCK'),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['role_a_id'], ['roles.id']),
        sa.ForeignKeyConstraint(['role_b_id'], ['roles.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'role_a_id', 'role_b_id', 'scope_type', name='uq_iam_role_conflict'
        ),
    )
    op.create_index('ix_iam_role_conflicts_role_a_id', 'iam_role_conflicts', ['role_a_id'], unique=False)
    op.create_index('ix_iam_role_conflicts_role_b_id', 'iam_role_conflicts', ['role_b_id'], unique=False)

    _backfill_role_profiles_and_assignments()


def _backfill_role_profiles_and_assignments():
    bind = op.get_bind()
    instant = datetime.utcnow()

    role_profiles = sa.table(
        'role_profiles',
        sa.column('role_id', sa.Integer()),
        sa.column('description', sa.Text()),
        sa.column('scope_types', sa.JSON()),
        sa.column('permission_patterns', sa.JSON()),
        sa.column('inheritance_enabled', sa.Boolean()),
        sa.column('is_system', sa.Boolean()),
        sa.column('enabled', sa.Boolean()),
        sa.column('assignable_by', sa.JSON()),
        sa.column('created_at', sa.DateTime()),
        sa.column('updated_at', sa.DateTime()),
    )
    roles = bind.execute(sa.text('SELECT id, name FROM roles')).mappings().all()
    if roles:
        bind.execute(role_profiles.insert(), [{
            'role_id': row['id'],
            'description': '',
            'scope_types': SCOPE_TYPES,
            'permission_patterns': [],
            'inheritance_enabled': True,
            'is_system': row['name'] in SYSTEM_ROLE_NAMES,
            'enabled': True,
            'assignable_by': [],
            'created_at': instant,
            'updated_at': instant,
        } for row in roles])

    assignments = sa.table(
        'iam_role_assignments',
        sa.column('id', sa.String()),
        sa.column('subject_type', sa.String()),
        sa.column('subject_id', sa.String()),
        sa.column('role_id', sa.Integer()),
        sa.column('effect', sa.String()),
        sa.column('scope_type', sa.String()),
        sa.column('scope_id', sa.String()),
        sa.column('tenant_id', sa.String()),
        sa.column('project_id', sa.String()),
        sa.column('apmid', sa.String()),
        sa.column('environment', sa.String()),
        sa.column('conditions', sa.JSON()),
        sa.column('permission_ceiling', sa.JSON()),
        sa.column('inherit', sa.Boolean()),
        sa.column('approval_required', sa.Boolean()),
        sa.column('valid_from', sa.DateTime()),
        sa.column('valid_until', sa.DateTime()),
        sa.column('enabled', sa.Boolean()),
        sa.column('source', sa.String()),
        sa.column('source_ref', sa.String()),
        sa.column('created_by', sa.Integer()),
        sa.column('created_at', sa.DateTime()),
        sa.column('updated_at', sa.DateTime()),
    )

    # Global user-role rows remain authoritative for backward compatibility.
    # Mirror them for the new Access UI; a NULL ceiling intentionally preserves
    # their existing live-role semantics.
    global_rows = bind.execute(sa.text(
        'SELECT user_id, role_id FROM user_roles'
    )).mappings().all()
    if global_rows:
        bind.execute(assignments.insert(), [{
            'id': str(uuid.uuid4()),
            'subject_type': 'USER',
            'subject_id': str(row['user_id']),
            'role_id': row['role_id'],
            'effect': 'ALLOW',
            'scope_type': 'GLOBAL',
            'scope_id': None,
            'tenant_id': None,
            'project_id': None,
            'apmid': None,
            'environment': None,
            'conditions': {},
            'permission_ceiling': None,
            'inherit': True,
            'approval_required': False,
            'valid_from': None,
            'valid_until': None,
            'enabled': True,
            'source': 'MIGRATION',
            'source_ref': f"user_roles:{row['user_id']}:{row['role_id']}",
            'created_by': None,
            'created_at': instant,
            'updated_at': instant,
        } for row in global_rows])

    tenant_rows = bind.execute(sa.text(
        """
        SELECT a.id, a.tenant_id, a.user_id, a.role_id, a.created_by,
               a.created_at, a.updated_at,
               p.name AS permission_name
        FROM tenant_role_assignments a
        LEFT JOIN tenant_role_grants g ON g.assignment_id = a.id
        LEFT JOIN permissions p ON p.id = g.permission_id
        ORDER BY a.id
        """
    )).mappings().all()
    _backfill_scoped(bind, assignments, tenant_rows, 'ORGANIZATION', 'tenant_id')

    project_rows = bind.execute(sa.text(
        """
        SELECT a.id, a.tenant_id, a.project_id, a.user_id, a.role_id, a.created_by,
               a.created_at, a.updated_at,
               p.name AS permission_name
        FROM project_role_assignments a
        LEFT JOIN project_role_grants g ON g.assignment_id = a.id
        LEFT JOIN permissions p ON p.id = g.permission_id
        ORDER BY a.id
        """
    )).mappings().all()
    _backfill_scoped(bind, assignments, project_rows, 'PROJECT', 'project_id')


def _backfill_scoped(bind, assignments, rows, scope_type, scope_key):
    grouped = {}
    for row in rows:
        key = row['id']
        item = grouped.setdefault(key, {
            'id': str(uuid.uuid4()),
            'subject_type': 'USER',
            'subject_id': str(row['user_id']),
            'role_id': row['role_id'],
            'effect': 'ALLOW',
            'scope_type': scope_type,
            'scope_id': row[scope_key],
            'tenant_id': row.get('tenant_id'),
            'project_id': row.get('project_id'),
            'apmid': None,
            'environment': None,
            'conditions': {},
            'permission_ceiling': [],
            'inherit': True,
            'approval_required': False,
            'valid_from': None,
            'valid_until': None,
            'enabled': True,
            'source': 'MIGRATION',
            'source_ref': f"{scope_type.lower()}:{row['id']}",
            'created_by': row.get('created_by'),
            'created_at': row.get('created_at') or datetime.utcnow(),
            'updated_at': row.get('updated_at') or row.get('created_at') or datetime.utcnow(),
        })
        if row.get('permission_name'):
            item['permission_ceiling'].append(row['permission_name'])
    if grouped:
        bind.execute(assignments.insert(), list(grouped.values()))


def downgrade():
    op.drop_table('iam_role_conflicts')
    op.drop_table('iam_break_glass')
    op.drop_table('iam_jit_requests')
    op.drop_table('iam_role_assignments')
    op.drop_table('iam_group_members')
    op.drop_table('iam_groups')
    op.drop_table('role_profiles')
