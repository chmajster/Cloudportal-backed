from fastapi import HTTPException
from sqlalchemy import select
from app.models import Permission, Role, Setting, User
from app.security.core import effective_permissions

PERMISSIONS = {
    **{area: actions.split() for area, actions in {
        'users': 'read create update delete', 'roles': 'read create update delete assign',
        'credentials': 'read create update delete test', 'providers': 'read create update delete',
        'deployments': 'read create destroy', 'jobs': 'read execute cancel',
        'terraform': 'read execute', 'ansible': 'read execute', 'audit': 'read',
        'blueprints': 'read create update delete execute',
        'hostnames': 'read create update delete reserve release',
        'vms': 'read power update delete clone migrate template',
        'snapshots': 'read create delete rollback',
        'ipam': 'read create update delete allocate release',
        'inventory': 'read import update delete',
        'tokens': 'read create revoke', 'settings': 'read update', 'portal': 'connect',
    }.items()}
}
ALL_PERMISSIONS = {f'{area}.{action}' for area, actions in PERMISSIONS.items() for action in actions}


def seed(db):
    existing = {p.name: p for p in db.scalars(select(Permission))}
    new_permission_names = ALL_PERMISSIONS - existing.keys()
    for name in sorted(new_permission_names):
        p = Permission(name=name)
        db.add(p)
        existing[name] = p
    db.flush()
    defaults = {
        'Administrator': ALL_PERMISSIONS,
        'Infrastructure Administrator': {p for p in ALL_PERMISSIONS if p.split('.')[0] not in {'users', 'roles', 'tokens', 'settings'}},
        'Operator': {'providers.read', 'credentials.read', 'deployments.read', 'deployments.create', 'jobs.read', 'jobs.execute', 'jobs.cancel', 'terraform.read', 'terraform.execute', 'ansible.read', 'ansible.execute', 'blueprints.read', 'blueprints.execute', 'hostnames.read', 'hostnames.reserve', 'hostnames.release', 'vms.read', 'vms.power', 'vms.update', 'vms.clone', 'vms.migrate', 'snapshots.read', 'snapshots.create', 'snapshots.rollback', 'ipam.read', 'ipam.allocate', 'ipam.release', 'inventory.read', 'inventory.import', 'inventory.update'},
        'Viewer': {'providers.read', 'deployments.read', 'jobs.read', 'terraform.read', 'ansible.read', 'blueprints.read', 'hostnames.read', 'vms.read', 'snapshots.read', 'ipam.read', 'inventory.read'},
        'Auditor': {'audit.read', 'users.read', 'roles.read', 'jobs.read', 'deployments.read', 'blueprints.read', 'hostnames.read', 'vms.read', 'snapshots.read', 'ipam.read', 'inventory.read'},
        'Portal Service': {'portal.connect'},
    }
    for name, permissions in defaults.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if not role:
            db.add(Role(name=name, permissions=[existing[p] for p in sorted(permissions)]))
        else:
            # Existing installations receive only newly introduced built-in permissions.
            current = {p.name for p in role.permissions}
            additions = permissions & new_permission_names - current
            role.permissions.extend(existing[p] for p in sorted(additions))
    if not db.get(Setting, 'governance'):
        db.add(Setting(key='governance', value={}))
    db.flush()


def permissions_from_names(db, names):
    if not set(names) <= ALL_PERMISSIONS:
        raise HTTPException(422, 'Unknown permission')
    return db.scalars(select(Permission).where(Permission.name.in_(set(names)))).all()


def governance_lock(db):
    db.scalar(select(Setting).where(Setting.key == 'governance').with_for_update())


def ensure_admin_remains(db):
    db.flush()
    users = db.scalars(select(User).where(User.is_active.is_(True), User.is_locked.is_(False), User.is_service_account.is_(False))).all()
    if not any(ALL_PERMISSIONS <= effective_permissions(u) for u in users):
        raise HTTPException(409, 'At least one active human administrator with all permissions must remain')
