from fastapi import HTTPException
from sqlalchemy import select
from app.models import Permission, Role, Setting, User, now
from app.security.core import effective_permissions
from app.rbac.locking import governance_lock
from app.projects.permissions import PROJECT_DEFAULT_ROLES
from app.tenancy.permissions import TENANCY_PERMISSION_ACTIONS, TENANCY_DEFAULT_ROLES


# Central permission catalog. Permission identifiers are stable API contracts:
# endpoint code, role definitions, API tokens, IAM assignments and UI capability
# checks all consume this catalog rather than inventing strings locally.
PERMISSIONS = {
    **{area: actions.split() for area, actions in {
        'users': 'read create update delete assign_roles impersonate',
        'roles': 'read create update delete assign delegate',
        'groups': 'read create update delete manage_members map_identity',
        'iam': 'assign subject.read roles.read binding.read binding.create binding.update binding.delete',
        'service_accounts': 'read create update delete tokens.manage',
        'rbac': 'assignments.read assignments.manage access_review.read impact.read',
        'authorization': 'check explain simulate',
        'jit': 'request read approve reject manage',
        'break_glass': 'use read manage',
        'credentials': 'read read_metadata read_secret use create update delete test',
        'providers': 'read create update delete test',
        'deployments': 'read read_all create delete destroy retry adopt approval.bypass',
        'jobs': 'read read_all execute cancel retry force',
        'terraform': 'read execute manage',
        'ansible': 'read execute manage',
        'awx': 'read execute manage',
        'audit': 'read',
        'blueprints': 'read create update delete execute publish clone manage_access approve',
        'hostnames': 'read create update delete reserve release',
        # vms.* is the legacy compatibility surface. New code should prefer
        # machines.* for Day-2 authorization granularity.
        'vms': 'read read_all manage_all power update delete clone migrate template console',
        'machines': (
            'read create update delete delete.force '
            'power.on power.off power.reset power.hard_stop power.suspend power.resume '
            'snapshot.create snapshot.delete snapshot.restore '
            'compute.resize disk.add disk.resize disk.delete disk.migrate '
            'network.add network.update network.delete console.open '
            'cloud_init.update credentials.inject packages.manage tags.update metadata.update '
            'rebuild rebuild.force clone migrate actions.cancel actions.retry'
        ),
        'snapshots': 'read create delete rollback',
        'backups': 'read create restore',
        'instance_backups': 'read create download upload verify delete restore',
        'ipam': 'read create update delete allocate release',
        'inventory': 'read read_all import update delete',
        'availability': 'read create update delete assign',
        'day2': (
            'view power snapshot.create snapshot.restore snapshot.delete compute.resize '
            'disk.add disk.resize disk.delete network.manage cloudinit.update credentials.manage '
            'ansible.run package.manage tags.manage metadata.manage migrate clone rebuild delete '
            'cancel retry approve admin override_protection'
        ),
        'resource_pools': 'read use manage placement.override',
        'approvals': 'read request approve reject manage',
        'schedules': 'read create update delete manage',
        'webhooks': 'read create update delete manage',
        'events': 'read publish replay consume manage',
        'event_broker': 'read manage',
        'extensions': 'read manage',
        'metrics': 'read',
        'quotas': 'read manage tenant.manage override',
        'policies': 'read manage simulate audit exception.manage',
        'tokens': 'read create revoke manage_scopes',
        'settings': 'read update',
        'updates': 'read execute update',
        'portal': 'connect',
    }.items()}
}
PERMISSIONS.update(TENANCY_PERMISSION_ACTIONS)
# Organization is the UI/IAM term; tenants.* remains the compatibility contract
# used by existing tenancy routes.
PERMISSIONS['organizations'] = 'read create update delete manage_members assign_roles audit.read'.split()
ALL_PERMISSIONS = {f'{area}.{action}' for area, actions in PERMISSIONS.items() for action in actions}

SYSTEM_ROLE_NAMES = frozenset({
    'Administrator', 'Global Administrator', 'Infrastructure Administrator',
    'Operator', 'Viewer', 'Read Only', 'Auditor', 'Organization Administrator',
    'Organization Auditor', 'Tenant Administrator', 'Tenant Viewer',
    'Project Administrator', 'Project Viewer', 'Project Operator', 'Developer',
    'Infrastructure Operator', 'Security Administrator', 'Approval Manager',
    'Blueprint Administrator', 'Credential Administrator', 'Self Service User',
    'Portal Service',
})


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
    machine_operator = {
        p for p in ALL_PERMISSIONS
        if p.startswith((
            'machines.read', 'machines.power.', 'machines.snapshot.', 'machines.compute.',
            'machines.disk.', 'machines.network.', 'machines.console.', 'machines.cloud_init.',
            'machines.credentials.', 'machines.packages.', 'machines.tags.', 'machines.metadata.',
            'machines.clone', 'machines.migrate', 'machines.actions.',
        ))
        and p not in {'machines.delete.force', 'machines.rebuild.force'}
    }
    viewer = {
        p for p in ALL_PERMISSIONS
        if p.endswith('.read') or '.read.' in p or p in {
            'projects.select', 'projects.use', 'credentials.read_metadata',
        }
    }
    developer = {
        'projects.read', 'projects.use', 'projects.select',
        'blueprints.read', 'blueprints.execute', 'deployments.read', 'deployments.create',
        'jobs.read', 'jobs.execute', 'jobs.cancel',
        'machines.read', 'machines.power.on', 'machines.power.off', 'machines.power.reset',
        'machines.snapshot.create', 'machines.snapshot.restore', 'machines.snapshot.delete',
        'machines.console.open', 'machines.tags.update', 'machines.metadata.update',
        'terraform.read', 'terraform.execute', 'ansible.read', 'ansible.execute', 'awx.read', 'awx.execute',
        'credentials.read_metadata', 'credentials.use', 'resource_pools.read', 'resource_pools.use',
    }
    defaults = {
        'Administrator': ALL_PERMISSIONS,
        'Global Administrator': ALL_PERMISSIONS,
        'Infrastructure Administrator': ({
            p for p in ALL_PERMISSIONS
            if p.split('.')[0] not in {
                'users', 'roles', 'groups', 'service_accounts', 'tokens', 'settings',
                'updates', 'tenants', 'organizations', 'projects', 'governance',
                'rbac', 'authorization', 'break_glass', 'instance_backups',
            } and p != 'quotas.tenant.manage'
        } | {'updates.read'}),
        'Operator': {
            'providers.read', 'credentials.read_metadata', 'credentials.use',
            'deployments.read', 'deployments.read_all', 'deployments.create',
            'jobs.read', 'jobs.read_all', 'jobs.execute', 'jobs.cancel', 'jobs.retry',
            'terraform.read', 'terraform.execute', 'ansible.read', 'ansible.execute',
            'blueprints.read', 'blueprints.execute', 'hostnames.read', 'hostnames.reserve', 'hostnames.release',
            'vms.read', 'vms.read_all', 'vms.manage_all', 'vms.power', 'vms.update', 'vms.clone',
            'vms.migrate', 'vms.console', 'snapshots.read', 'snapshots.create', 'snapshots.rollback',
            'backups.read', 'backups.create', 'backups.restore', 'ipam.read', 'ipam.allocate',
            'ipam.release', 'inventory.read', 'inventory.read_all', 'inventory.import', 'inventory.update',
            'availability.read', 'availability.assign', 'schedules.read', 'schedules.create',
            'schedules.update', 'events.read', 'extensions.read', 'quotas.read', 'policies.read',
            'resource_pools.read', 'resource_pools.use',
        } | day2_operator | machine_operator,
        'Viewer': viewer,
        'Read Only': viewer,
        'Auditor': viewer | {
            'audit.read', 'users.read', 'roles.read', 'groups.read', 'rbac.access_review.read',
            'authorization.explain', 'policies.audit', 'events.read', 'metrics.read', 'updates.read',
        },
        'Organization Administrator': set(TENANCY_DEFAULT_ROLES['Tenant Administrator']),
        'Organization Auditor': {
            'tenants.read', 'tenants.members.read', 'tenants.audit.read',
            'organizations.read', 'organizations.audit.read',
            'projects.read', 'projects.members.read', 'projects.audit.read',
        },
        'Project Operator': set(PROJECT_DEFAULT_ROLES['Project Operator']) | machine_operator,
        'Developer': developer,
        'Infrastructure Operator': machine_operator | {
            'providers.read', 'credentials.read_metadata', 'credentials.use',
            'deployments.read', 'jobs.read', 'ansible.read', 'ansible.execute', 'awx.read', 'awx.execute',
        },
        'Security Administrator': {
            'users.read', 'roles.read', 'groups.read', 'groups.map_identity',
            'iam.assign', 'iam.subject.read', 'iam.roles.read', 'iam.binding.read',
            'iam.binding.create', 'iam.binding.update', 'iam.binding.delete',
            'rbac.assignments.read', 'rbac.assignments.manage', 'rbac.access_review.read',
            'authorization.check', 'authorization.explain', 'authorization.simulate',
            'audit.read', 'policies.read', 'policies.manage', 'policies.simulate', 'policies.audit',
            'tokens.read', 'tokens.revoke', 'break_glass.read', 'break_glass.manage',
        },
        'Approval Manager': {
            'approvals.read', 'approvals.approve', 'approvals.reject', 'approvals.manage',
            'jit.read', 'jit.approve', 'jit.reject', 'jobs.read',
        },
        'Blueprint Administrator': {
            'blueprints.read', 'blueprints.create', 'blueprints.update', 'blueprints.delete',
            'blueprints.execute', 'blueprints.publish', 'blueprints.clone', 'blueprints.manage_access',
        },
        'Credential Administrator': {
            'credentials.read', 'credentials.read_metadata', 'credentials.create',
            'credentials.update', 'credentials.delete', 'credentials.test', 'credentials.use',
        },
        'Self Service User': {
            'projects.read', 'projects.use', 'projects.select', 'blueprints.read', 'blueprints.execute',
            'deployments.read', 'jobs.read', 'machines.read', 'credentials.use',
            'credentials.read_metadata', 'resource_pools.read', 'resource_pools.use',
            'jit.request',
        },
        'Portal Service': {'portal.connect'},
        **TENANCY_DEFAULT_ROLES,
        **PROJECT_DEFAULT_ROLES,
    }
    for name, permissions in defaults.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if not role:
            db.add(Role(name=name, permissions=[existing[p] for p in sorted(permissions)]))
        else:
            current_permissions = {p.name for p in role.permissions}
            if name in {'Administrator', 'Global Administrator'}:
                additions = permissions - current_permissions
            else:
                # Existing customized built-ins are not reset. Only newly introduced
                # catalog entries are added when they belong to the default template.
                additions = permissions & new_permission_names - current_permissions
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
    users = db.scalars(select(User).where(
        User.is_active.is_(True),
        User.is_locked.is_(False),
        User.is_service_account.is_(False),
    )).all()
    if any(ALL_PERMISSIONS <= effective_permissions(u) for u in users):
        return

    # Generic enterprise assignments may be the authoritative global-admin
    # grant after migration away from legacy UserRole. Import lazily to avoid a
    # module cycle during bootstrap.
    from sqlalchemy import and_, or_
    from app.iam.models import Group, GroupMember, RoleAssignment, RoleProfile
    instant = now()
    candidates = db.scalars(
        select(RoleAssignment).where(
            RoleAssignment.effect == 'ALLOW',
            RoleAssignment.scope_type == 'GLOBAL',
            RoleAssignment.enabled.is_(True),
            or_(RoleAssignment.valid_from.is_(None), RoleAssignment.valid_from <= instant),
            RoleAssignment.valid_until.is_(None),
            RoleAssignment.subject_type.in_(('USER', 'GROUP')),
        )
    ).all()
    for assignment in candidates:
        profile = db.get(RoleProfile, assignment.role_id)
        if profile is not None and not profile.enabled:
            continue
        role = db.get(Role, assignment.role_id)
        if role is None or not ALL_PERMISSIONS <= {p.name for p in role.permissions}:
            continue
        if assignment.permission_ceiling is not None and not ALL_PERMISSIONS <= set(assignment.permission_ceiling):
            continue
        if assignment.subject_type == 'USER' and assignment.subject_id.isdigit():
            user = db.get(User, int(assignment.subject_id))
            if (user is not None and user.is_active and not user.is_locked
                    and not user.is_service_account):
                return
        if assignment.subject_type == 'GROUP':
            group = db.get(Group, assignment.subject_id)
            if group is None or not group.enabled:
                continue
            member = db.scalar(
                select(User.id)
                .join(GroupMember, GroupMember.user_id == User.id)
                .where(
                    GroupMember.group_id == assignment.subject_id,
                    User.is_active.is_(True),
                    User.is_locked.is_(False),
                    User.is_service_account.is_(False),
                )
                .limit(1)
            )
            if member is not None:
                return
    raise HTTPException(409, 'At least one active human administrator with all permissions must remain')
