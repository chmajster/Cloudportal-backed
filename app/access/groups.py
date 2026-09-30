from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.iam.models import Group, RoleAssignment
from app.models import Role


ROLE_DESCRIPTIONS = {
    'Global Administrator': 'Pełne zarządzanie całą platformą.',
    'Organization Administrator': 'Pełne zarządzanie w wybranej organizacji.',
    'Organization Auditor': 'Odczyt, audyt i wyjaśnienie decyzji dostępu.',
    'Project Administrator': 'Pełne zarządzanie w wybranym projekcie.',
    'Project Operator': 'Codzienne operacje na zasobach bez administracji IAM.',
    'Project Viewer': 'Tylko odczyt zasobów projektu.',
    'Self Service User': 'Uruchamianie dozwolonych produktów i blueprintów.',
}


def _managed_group(db, *, system_key: str, name: str, managed_type: str, created_by=None) -> Group:
    row = db.scalar(select(Group).where(Group.system_key == system_key))
    if row is not None:
        if not row.name:
            row.name = name
        row.managed_type = managed_type
        return row

    display_name = name
    conflict = db.scalar(select(Group.id).where(Group.name == display_name))
    if conflict is not None:
        display_name = f'{name} · {system_key[-8:]}'
    try:
        with db.begin_nested():
            row = Group(
                name=display_name, description='Grupa zarządzana przez CloudPortal.',
                enabled=True, system_key=system_key, managed_type=managed_type,
                created_by=created_by,
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        row = db.scalar(select(Group).where(Group.system_key == system_key))
        if row is None:
            raise
        return row


def _managed_binding(
    db, group: Group, role_name: str, *, scope_type: str,
    organization_id: str | None = None, project_id: str | None = None,
    apmid: str | None = None, created_by=None,
) -> RoleAssignment | None:
    role = db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        return None
    source_ref = f'managed-group:{group.system_key}'
    row = db.scalar(select(RoleAssignment).where(RoleAssignment.source_ref == source_ref))
    ceiling = sorted({permission.name for permission in role.permissions})
    scope_id = (
        project_id if scope_type == 'PROJECT'
        else organization_id if scope_type == 'ORGANIZATION'
        else apmid if scope_type == 'APMID'
        else None
    )
    if row is None:
        row = RoleAssignment(
            subject_type='GROUP', subject_id=group.id, role_id=role.id,
            effect='ALLOW', scope_type=scope_type, scope_id=scope_id,
            tenant_id=organization_id, project_id=project_id, apmid=apmid,
            conditions={}, permission_ceiling=ceiling, inherit=True,
            approval_required=False, enabled=group.enabled, source='SYSTEM',
            source_ref=source_ref, created_by=created_by,
        )
        db.add(row)
    else:
        row.subject_id = group.id
        row.role_id = role.id
        row.scope_type = scope_type
        row.scope_id = scope_id
        row.tenant_id = organization_id
        row.project_id = project_id
        row.apmid = apmid
        row.permission_ceiling = ceiling
        row.enabled = group.enabled
    db.flush()
    return row


def ensure_global_groups(db, *, created_by=None):
    group = _managed_group(
        db, system_key='global.admins', name='CloudPortal Administrators',
        managed_type='GLOBAL_ADMINISTRATORS', created_by=created_by,
    )
    _managed_binding(db, group, 'Global Administrator', scope_type='GLOBAL', created_by=created_by)
    return [group]


def ensure_organization_groups(db, organization, *, created_by=None):
    specs = (
        ('admins', 'Administrators', 'Organization Administrator', 'ORGANIZATION_ADMINISTRATORS'),
        ('auditors', 'Auditors', 'Organization Auditor', 'ORGANIZATION_AUDITORS'),
    )
    result = []
    for suffix, label, role_name, managed_type in specs:
        group = _managed_group(
            db, system_key=f'org:{organization.id}:{suffix}',
            name=f'{organization.name} / {label}', managed_type=managed_type,
            created_by=created_by,
        )
        _managed_binding(
            db, group, role_name, scope_type='ORGANIZATION',
            organization_id=str(organization.id), created_by=created_by,
        )
        result.append(group)
    return result


def ensure_project_groups(db, organization, project, *, created_by=None):
    specs = (
        ('admins', 'Administrators', 'Project Administrator', 'PROJECT_ADMINISTRATORS'),
        ('operators', 'Operators', 'Project Operator', 'PROJECT_OPERATORS'),
        ('viewers', 'Viewers', 'Project Viewer', 'PROJECT_VIEWERS'),
    )
    result = []
    for suffix, label, role_name, managed_type in specs:
        group = _managed_group(
            db, system_key=f'project:{project.id}:{suffix}',
            name=f'{organization.name} / {project.name} / {label}', managed_type=managed_type,
            created_by=created_by,
        )
        _managed_binding(
            db, group, role_name, scope_type='PROJECT', organization_id=str(organization.id),
            project_id=str(project.id), created_by=created_by,
        )
        result.append(group)
    return result


def ensure_apmid_groups(db, organization, apmid_code: str, *, created_by=None):
    code = str(apmid_code).upper()
    specs = (
        ('operators', 'Operators', 'Project Operator', 'APMID_OPERATORS'),
        ('viewers', 'Viewers', 'Project Viewer', 'APMID_VIEWERS'),
    )
    result = []
    for suffix, label, role_name, managed_type in specs:
        group = _managed_group(
            db, system_key=f'apmid:{organization.id}:{code}:{suffix}',
            name=f'{organization.name} / APMID {code} / {label}', managed_type=managed_type,
            created_by=created_by,
        )
        _managed_binding(
            db, group, role_name, scope_type='APMID', organization_id=str(organization.id),
            apmid=code, created_by=created_by,
        )
        result.append(group)
    return result


def disable_apmid_groups(db, organization_id: str, code: str) -> None:
    prefix = f'apmid:{organization_id}:{code.upper()}:'
    rows = db.scalars(select(Group).where(Group.system_key.like(prefix + '%'))).all()
    for group in rows:
        group.enabled = False
        for assignment in db.scalars(select(RoleAssignment).where(
            RoleAssignment.subject_type == 'GROUP', RoleAssignment.subject_id == group.id,
        )):
            assignment.enabled = False
    db.flush()
