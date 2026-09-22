from fastapi import HTTPException
from sqlalchemy import select
from app.models import Permission, Role, Setting, User
from app.security.core import effective_permissions
from app.rbac.locking import governance_lock
from app.projects.permissions import PROJECT_DEFAULT_ROLES
from app.tenancy.permissions import TENANCY_PERMISSION_ACTIONS, TENANCY_DEFAULT_ROLES

PERMISSIONS = {
    **{area: actions.split() for area, actions in {
        'users': 'read create update delete', 'roles': 'read create update delete assign',
        'credentials': 'read create update delete test', 'providers': 'read create update delete',
        'deployments': 'read read_all create destroy adopt', 'jobs': 'read read_all execute cancel',
        'terraform': 'read execute', 'ansible': 'read execute', 'audit': 'read',
        'blueprints': 'read create update delete execute approve',
        'hostnames': 'read create update delete reserve release',
        'vms': 'read read_all manage_all power update delete clone migrate template console',
        'snapshots': 'read create delete rollback',
        'backups': 'read create restore',
        'ipam': 'read create update delete allocate release',
        'inventory': 'read read_all import update delete',
        'day2': ('view power snapshot.create snapshot.restore snapshot.delete compute.resize disk.add disk.resize '
                 'disk.delete network.manage cloudinit.update credentials.manage ansible.run package.manage '
                 'tags.manage metadata.manage migrate clone rebuild delete cancel retry approve admin override_protection'),
        'schedules': 'read create update delete',
        'webhooks': 'read create update delete',
        'events': 'read publish replay consume manage', 'extensions': 'read manage',
        'metrics': 'read', 'quotas': 'read manage tenant.manage',
        'tokens': 'read create revoke', 'settings': 'read update', 'updates': 'read execute update', 'portal': 'connect',
    }.items()}
}
PERMISSIONS.update(TENANCY_PERMISSION_ACTIONS)
ALL_PERMISSIONS = {f'{area}.{action}' for area, actions in PERMISSIONS.items() for action in actions}


def seed(db):
    existing = {p.name: p for p in db.scalars(select(Permission))}
    new_permission_names = ALL_PERMISSIONS - existing.keys()
    for name in sorted(new_permission_names):
        p = Permission(name=name)
        db.add(p)
        existing[name] = p
    db.flush()
    day2_operator = {
        'day2.view', 'day2.power', 'day2.snapshot.create', 'day2.snapshot.restore', 'day2.snapshot.delete',
        'day2.compute.resize', 'day2.disk.add', 'day2.disk.resize', 'day2.disk.delete', 'day2.network.manage',
        'day2.cloudinit.update', 'day2.credentials.manage', 'day2.ansible.run', 'day2.package.manage',
        'day2.tags.manage', 'day2.metadata.manage', 'day2.migrate', 'day2.clone', 'day2.cancel', 'day2.retry',
    }
    defaults = {
        'Administrator': ALL_PERMISSIONS,
        'Infrastructure Administrator': ({p for p in ALL_PERMISSIONS if p.split('.')[0] not in {'users', 'roles', 'tokens', 'settings', 'updates', 'tenants', 'projects', 'governance'} and p != 'quotas.tenant.manage'} | {'updates.read'}),
        'Operator': {'providers.read', 'credentials.read', 'deployments.read', 'deployments.read_all', 'deployments.create', 'jobs.read', 'jobs.read_all', 'jobs.execute', 'jobs.cancel', 'terraform.read', 'terraform.execute', 'ansible.read', 'ansible.execute', 'blueprints.read', 'blueprints.execute', 'hostnames.read', 'hostnames.reserve', 'hostnames.release', 'vms.read', 'vms.read_all', 'vms.manage_all', 'vms.power', 'vms.update', 'vms.clone', 'vms.migrate', 'vms.console', 'snapshots.read', 'snapshots.create', 'snapshots.rollback', 'backups.read', 'backups.create', 'backups.restore', 'ipam.read', 'ipam.allocate', 'ipam.release', 'inventory.read', 'inventory.read_all', 'inventory.import', 'inventory.update', 'schedules.read', 'schedules.create', 'schedules.update', 'events.read', 'extensions.read', 'quotas.read'} | day2_operator,
        'Viewer': {'providers.read', 'deployments.read', 'deployments.read_all', 'jobs.read', 'jobs.read_all', 'terraform.read', 'ansible.read', 'blueprints.read', 'hostnames.read', 'vms.read', 'vms.read_all', 'snapshots.read', 'backups.read', 'ipam.read', 'inventory.read', 'inventory.read_all', 'schedules.read', 'day2.view', 'quotas.read'},
        'Auditor': {'audit.read', 'users.read', 'roles.read', 'jobs.read', 'jobs.read_all', 'deployments.read', 'deployments.read_all', 'blueprints.read', 'hostnames.read', 'vms.read', 'vms.read_all', 'snapshots.read', 'backups.read', 'ipam.read', 'inventory.read', 'inventory.read_all', 'schedules.read', 'events.read', 'extensions.read', 'metrics.read', 'updates.read', 'day2.view', 'quotas.read'},
        'Portal Service': {'portal.connect'},
        **TENANCY_DEFAULT_ROLES,
        **PROJECT_DEFAULT_ROLES,
    }
    for name, permissions in defaults.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if not role:
            db.add(Role(name=name, permissions=[existing[p] for p in sorted(permissions)]))
        else:
            current = {p.name for p in role.permissions}
            if name == 'Administrator':
                # The built-in Administrator role is authoritative and must always
                # receive every permission, including permissions that already exist
                # in the database but were missing from the role assignment.
                additions = permissions - current
            else:
                # Other built-in roles receive only newly introduced defaults so
                # administrators can intentionally customize them.
                additions = permissions & new_permission_names - current
            role.permissions.extend(existing[p] for p in sorted(additions))
    if not db.get(Setting, 'governance'):
        db.add(Setting(key='governance', value={}))
    db.flush()


def permissions_from_names(db, names):
    if not set(names) <= ALL_PERMISSIONS:
        raise HTTPException(422, 'Unknown permission')
    return db.scalars(select(Permission).where(Permission.name.in_(set(names)))).all()


def ensure_admin_remains(db):
    db.flush()
    users = db.scalars(select(User).where(User.is_active.is_(True), User.is_locked.is_(False), User.is_service_account.is_(False))).all()
    if not any(ALL_PERMISSIONS <= effective_permissions(u) for u in users):
        raise HTTPException(409, 'At least one active human administrator with all permissions must remain')
