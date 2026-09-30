"""unify organization access, managed groups and relational APMID

Revision ID: f1a44d7e9c21
Revises: 4f81b6c2d9a0
"""
from datetime import datetime
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'f1a44d7e9c21'
down_revision = '4f81b6c2d9a0'
branch_labels = None
depends_on = None

_NAMESPACE = uuid.UUID('57ce1e80-d71e-4f16-844d-21ea47d2a866')


def _uuid(*parts):
    return str(uuid.uuid5(_NAMESPACE, ':'.join(str(part) for part in parts)))


def _json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _codes(value):
    result = ['LEO']
    raw = _json(value)
    for item in raw.get('apmids') or []:
        code = str(item or '').strip().upper()
        if code and code not in result:
            result.append(code)
    return result


def _insert_apmid(bind, organization_id, code, *, system=False):
    exists = bind.execute(sa.text(
        'SELECT 1 FROM organization_apmids WHERE organization_id=:org AND code=:code'
    ), {'org': organization_id, 'code': code}).first()
    if exists:
        if code == 'LEO':
            bind.execute(sa.text(
                'UPDATE organization_apmids SET enabled=:enabled, is_system=:system '
                'WHERE organization_id=:org AND code=:code'
            ), {'enabled': True, 'system': True, 'org': organization_id, 'code': code})
        return
    instant = datetime.utcnow()
    bind.execute(sa.text(
        'INSERT INTO organization_apmids '
        '(id, organization_id, code, name, description, enabled, is_system, created_by, created_at, updated_at) '
        'VALUES (:id,:org,:code,:name,:description,:enabled,:system,NULL,:created,:updated)'
    ), {
        'id': _uuid('apmid', organization_id, code), 'org': organization_id,
        'code': code, 'name': code, 'description': '', 'enabled': True,
        'system': bool(system or code == 'LEO'), 'created': instant, 'updated': instant,
    })


def _role_id(bind, names):
    for name in names:
        row = bind.execute(sa.text('SELECT id FROM roles WHERE name=:name'), {'name': name}).first()
        if row:
            return row[0]
    return None


def _role_ceiling(bind, role_id):
    if role_id is None:
        return []
    return [row[0] for row in bind.execute(sa.text(
        'SELECT p.name FROM permissions p '
        'JOIN role_permissions rp ON rp.permission_id=p.id '
        'WHERE rp.role_id=:role_id ORDER BY p.name'
    ), {'role_id': role_id}).all()]


def _unique_group_name(bind, preferred, system_key):
    row = bind.execute(sa.text('SELECT system_key FROM iam_groups WHERE name=:name'), {'name': preferred}).first()
    if row is None or row[0] == system_key:
        return preferred
    return f'{preferred} · {system_key[-8:]}'


def _ensure_group(bind, system_key, preferred_name, managed_type):
    row = bind.execute(sa.text(
        'SELECT id FROM iam_groups WHERE system_key=:key'
    ), {'key': system_key}).first()
    if row:
        return row[0]
    group_id = _uuid('group', system_key)
    instant = datetime.utcnow()
    bind.execute(sa.text(
        'INSERT INTO iam_groups '
        '(id,name,description,external_source,external_id,system_key,managed_type,enabled,created_by,created_at,updated_at) '
        'VALUES (:id,:name,:description,NULL,NULL,:key,:managed_type,:enabled,NULL,:created,:updated)'
    ), {
        'id': group_id,
        'name': _unique_group_name(bind, preferred_name, system_key),
        'description': 'Grupa zarządzana przez CloudPortal.',
        'key': system_key, 'managed_type': managed_type, 'enabled': True,
        'created': instant, 'updated': instant,
    })
    return group_id


def _ensure_group_binding(bind, group_id, system_key, role_names, scope_type,
                          organization_id=None, project_id=None, apmid=None):
    role_id = _role_id(bind, role_names)
    if role_id is None:
        return
    source_ref = f'managed-group:{system_key}'
    if bind.execute(sa.text(
        'SELECT 1 FROM iam_role_assignments WHERE source_ref=:source_ref'
    ), {'source_ref': source_ref}).first():
        return
    scope_id = (
        project_id if scope_type == 'PROJECT'
        else organization_id if scope_type == 'ORGANIZATION'
        else apmid if scope_type == 'APMID'
        else None
    )
    instant = datetime.utcnow()
    statement = sa.text(
        'INSERT INTO iam_role_assignments '
        '(id,subject_type,subject_id,role_id,effect,scope_type,scope_id,tenant_id,project_id,apmid,environment,'
        'conditions,permission_ceiling,inherit,approval_required,valid_from,valid_until,enabled,source,source_ref,'
        'created_by,created_at,updated_at) '
        'VALUES (:id,:subject_type,:subject_id,:role_id,:effect,:scope_type,:scope_id,:tenant_id,:project_id,:apmid,NULL,'
        ':conditions,:ceiling,:inherit,:approval,NULL,NULL,:enabled,:source,:source_ref,NULL,:created,:updated)'
    ).bindparams(
        sa.bindparam('conditions', type_=sa.JSON()),
        sa.bindparam('ceiling', type_=sa.JSON()),
    )
    bind.execute(statement, {
        'id': _uuid('binding', system_key), 'subject_type': 'GROUP', 'subject_id': group_id,
        'role_id': role_id, 'effect': 'ALLOW', 'scope_type': scope_type, 'scope_id': scope_id,
        'tenant_id': organization_id, 'project_id': project_id, 'apmid': apmid,
        'conditions': {}, 'ceiling': _role_ceiling(bind, role_id),
        'inherit': True, 'approval': False, 'enabled': True, 'source': 'SYSTEM',
        'source_ref': source_ref, 'created': instant, 'updated': instant,
    })


def _ensure_managed_groups(bind):
    global_group = _ensure_group(bind, 'global.admins', 'CloudPortal Administrators', 'GLOBAL_ADMINISTRATORS')
    _ensure_group_binding(bind, global_group, 'global.admins',
                          ('Global Administrator', 'Administrator'), 'GLOBAL')

    organizations = bind.execute(sa.text(
        'SELECT id,name FROM tenants WHERE deleted_at IS NULL ORDER BY id'
    )).all()
    for organization_id, organization_name in organizations:
        specs = (
            ('admins', 'Administrators', ('Organization Administrator', 'Tenant Administrator'), 'ORGANIZATION_ADMINISTRATORS'),
            ('auditors', 'Auditors', ('Organization Auditor', 'Tenant Viewer'), 'ORGANIZATION_AUDITORS'),
        )
        for suffix, label, roles, managed_type in specs:
            key = f'org:{organization_id}:{suffix}'
            group = _ensure_group(bind, key, f'{organization_name} / {label}', managed_type)
            _ensure_group_binding(bind, group, key, roles, 'ORGANIZATION', organization_id=organization_id)

        for (code,) in bind.execute(sa.text(
            'SELECT code FROM organization_apmids WHERE organization_id=:org ORDER BY code'
        ), {'org': organization_id}).all():
            for suffix, label, roles, managed_type in (
                ('operators', 'Operators', ('Project Operator',), 'APMID_OPERATORS'),
                ('viewers', 'Viewers', ('Project Viewer',), 'APMID_VIEWERS'),
            ):
                key = f'apmid:{organization_id}:{code}:{suffix}'
                group = _ensure_group(bind, key, f'{organization_name} / APMID {code} / {label}', managed_type)
                _ensure_group_binding(
                    bind, group, key, roles, 'APMID',
                    organization_id=organization_id, apmid=code,
                )

    projects = bind.execute(sa.text(
        'SELECT p.id,p.tenant_id,p.name,t.name '
        'FROM projects p JOIN tenants t ON t.id=p.tenant_id '
        'WHERE p.deleted_at IS NULL AND t.deleted_at IS NULL ORDER BY p.id'
    )).all()
    for project_id, organization_id, project_name, organization_name in projects:
        for suffix, label, roles, managed_type in (
            ('admins', 'Administrators', ('Project Administrator',), 'PROJECT_ADMINISTRATORS'),
            ('operators', 'Operators', ('Project Operator',), 'PROJECT_OPERATORS'),
            ('viewers', 'Viewers', ('Project Viewer',), 'PROJECT_VIEWERS'),
        ):
            key = f'project:{project_id}:{suffix}'
            group = _ensure_group(bind, key, f'{organization_name} / {project_name} / {label}', managed_type)
            _ensure_group_binding(
                bind, group, key, roles, 'PROJECT',
                organization_id=organization_id, project_id=project_id,
            )


def _backfill_apmids(bind):
    global_row = bind.execute(sa.text(
        "SELECT value FROM settings WHERE key='vm_classification'"
    )).first()
    global_value = global_row[0] if global_row else {}
    for (organization_id,) in bind.execute(sa.text(
        'SELECT id FROM tenants WHERE deleted_at IS NULL ORDER BY id'
    )).all():
        legacy = bind.execute(sa.text(
            'SELECT value FROM settings WHERE key=:key'
        ), {'key': f'vm_classification:{organization_id}'}).first()
        source = legacy[0] if legacy else global_value
        for code in _codes(source):
            _insert_apmid(bind, organization_id, code, system=(code == 'LEO'))


def _insert_legacy_binding(bind, *, source_ref, user_id, role_id, scope_type,
                           organization_id=None, project_id=None, ceiling=None, created_by=None):
    if bind.execute(sa.text(
        'SELECT 1 FROM iam_role_assignments WHERE source_ref=:source_ref'
    ), {'source_ref': source_ref}).first():
        return
    instant = datetime.utcnow()
    scope_id = organization_id if scope_type == 'ORGANIZATION' else project_id if scope_type == 'PROJECT' else None
    statement = sa.text(
        'INSERT INTO iam_role_assignments '
        '(id,subject_type,subject_id,role_id,effect,scope_type,scope_id,tenant_id,project_id,apmid,environment,'
        'conditions,permission_ceiling,inherit,approval_required,valid_from,valid_until,enabled,source,source_ref,'
        'created_by,created_at,updated_at) '
        'VALUES (:id,:subject_type,:subject_id,:role_id,:effect,:scope_type,:scope_id,:tenant_id,:project_id,NULL,NULL,'
        ':conditions,:ceiling,:inherit,:approval,NULL,NULL,:enabled,:source,:source_ref,:created_by,:created,:updated)'
    ).bindparams(
        sa.bindparam('conditions', type_=sa.JSON()),
        sa.bindparam('ceiling', type_=sa.JSON()),
    )
    bind.execute(statement, {
        'id': _uuid('legacy-binding', source_ref), 'subject_type': 'USER', 'subject_id': str(user_id),
        'role_id': role_id, 'effect': 'ALLOW', 'scope_type': scope_type, 'scope_id': scope_id,
        'tenant_id': organization_id, 'project_id': project_id,
        'conditions': {}, 'ceiling': ceiling,
        'inherit': True, 'approval': False, 'enabled': True, 'source': 'MIGRATION',
        'source_ref': source_ref, 'created_by': created_by, 'created': instant, 'updated': instant,
    })


def _repair_legacy_bindings(bind):
    for user_id, role_id in bind.execute(sa.text(
        'SELECT user_id,role_id FROM user_roles ORDER BY user_id,role_id'
    )).all():
        _insert_legacy_binding(
            bind, source_ref=f'user_roles:{user_id}:{role_id}', user_id=user_id,
            role_id=role_id, scope_type='GLOBAL', ceiling=None,
        )

    tenant_rows = bind.execute(sa.text(
        'SELECT id,tenant_id,user_id,role_id,created_by FROM tenant_role_assignments ORDER BY id'
    )).all()
    for assignment_id, organization_id, user_id, role_id, created_by in tenant_rows:
        ceiling = [row[0] for row in bind.execute(sa.text(
            'SELECT p.name FROM permissions p JOIN tenant_role_grants g ON g.permission_id=p.id '
            'WHERE g.assignment_id=:id ORDER BY p.name'
        ), {'id': assignment_id}).all()]
        _insert_legacy_binding(
            bind, source_ref=f'organization:{assignment_id}', user_id=user_id, role_id=role_id,
            scope_type='ORGANIZATION', organization_id=organization_id,
            ceiling=ceiling, created_by=created_by,
        )

    project_rows = bind.execute(sa.text(
        'SELECT id,tenant_id,project_id,user_id,role_id,created_by FROM project_role_assignments ORDER BY id'
    )).all()
    for assignment_id, organization_id, project_id, user_id, role_id, created_by in project_rows:
        ceiling = [row[0] for row in bind.execute(sa.text(
            'SELECT p.name FROM permissions p JOIN project_role_grants g ON g.permission_id=p.id '
            'WHERE g.assignment_id=:id ORDER BY p.name'
        ), {'id': assignment_id}).all()]
        _insert_legacy_binding(
            bind, source_ref=f'project:{assignment_id}', user_id=user_id, role_id=role_id,
            scope_type='PROJECT', organization_id=organization_id, project_id=project_id,
            ceiling=ceiling, created_by=created_by,
        )


def upgrade():
    op.add_column('iam_groups', sa.Column('system_key', sa.String(length=255), nullable=True))
    op.add_column('iam_groups', sa.Column('managed_type', sa.String(length=64), nullable=True))
    op.create_index('uq_iam_groups_system_key', 'iam_groups', ['system_key'], unique=True)
    op.create_index('ix_iam_groups_managed_type', 'iam_groups', ['managed_type', 'enabled'], unique=False)

    op.add_column('projects', sa.Column('allowed_apmids', sa.JSON(), nullable=True))

    op.create_table(
        'organization_apmids',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=63), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['tenants.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'code', name='uq_organization_apmids_org_code'),
    )
    op.create_index('ix_organization_apmids_organization_id', 'organization_apmids', ['organization_id'])
    op.create_index('ix_organization_apmids_enabled', 'organization_apmids', ['enabled'])
    op.create_index('ix_organization_apmids_is_system', 'organization_apmids', ['is_system'])
    op.create_index(
        'ix_organization_apmids_org_enabled', 'organization_apmids',
        ['organization_id', 'enabled', 'code'], unique=False,
    )

    bind = op.get_bind()
    _backfill_apmids(bind)
    _repair_legacy_bindings(bind)
    _ensure_managed_groups(bind)


def downgrade():
    op.drop_index('ix_organization_apmids_org_enabled', table_name='organization_apmids')
    op.drop_index('ix_organization_apmids_is_system', table_name='organization_apmids')
    op.drop_index('ix_organization_apmids_enabled', table_name='organization_apmids')
    op.drop_index('ix_organization_apmids_organization_id', table_name='organization_apmids')
    op.drop_table('organization_apmids')
    op.drop_column('projects', 'allowed_apmids')
    op.drop_index('ix_iam_groups_managed_type', table_name='iam_groups')
    op.drop_index('uq_iam_groups_system_key', table_name='iam_groups')
    op.drop_column('iam_groups', 'managed_type')
    op.drop_column('iam_groups', 'system_key')
