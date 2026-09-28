"""Only resource operations are delegable. Platform configuration remains global."""

RESOURCE_ACTIONS = {
    'deployments': 'read read_all create delete destroy retry adopt approval.bypass',
    'jobs': 'read read_all execute cancel retry',
    'terraform': 'read execute',
    'ansible': 'read execute',
    'awx': 'read execute',
    'inventory': 'read read_all import update delete',
    'availability': 'read create update delete assign',
    'blueprints': 'read create update delete execute publish clone manage_access approve',
    'hostnames': 'read create update delete reserve release',
    # vms.* stays available while legacy routes migrate to machines.*.
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
    'ipam': 'read create update delete allocate release',
    'schedules': 'read create update delete',
    'providers': 'read',
    # Reading credential metadata and using a credential are distinct from
    # reading the secret. read_secret is intentionally not delegable here.
    'credentials': 'read read_metadata use test',
    'resource_pools': 'read use manage placement.override',
    'approvals': 'read request approve reject',
    'quotas': 'read manage tenant.manage override',
    'policies': 'read manage simulate audit exception.manage',
}
RESOURCE_PERMISSIONS = frozenset(
    f'{area}.{action}'
    for area, actions in RESOURCE_ACTIONS.items()
    for action in actions.split()
)
SCOPE_PERMISSION_ACTIONS = {'governance': ['admin', 'access.read', 'access.manage']}

# Request acceptance is not proof that arbitrary provider targets are confined
# to the selected project. Mutating execution surfaces retain the execution
# fence until their provider adapter has an explicit governed target.
EXECUTION_PERMISSIONS = frozenset({
    'deployments.create', 'deployments.delete', 'deployments.destroy',
    'deployments.retry', 'deployments.adopt',
    'jobs.execute', 'jobs.cancel', 'jobs.retry',
    'terraform.execute', 'ansible.execute', 'awx.execute',
    'blueprints.execute', 'inventory.import', 'inventory.update', 'inventory.delete',
    'availability.assign', 'resource_pools.use',
    'vms.power', 'vms.update', 'vms.delete', 'vms.clone', 'vms.migrate', 'vms.template',
    'machines.create', 'machines.update', 'machines.delete', 'machines.delete.force',
    'machines.power.on', 'machines.power.off', 'machines.power.reset',
    'machines.power.hard_stop', 'machines.power.suspend', 'machines.power.resume',
    'machines.snapshot.create', 'machines.snapshot.delete', 'machines.snapshot.restore',
    'machines.compute.resize', 'machines.disk.add', 'machines.disk.resize',
    'machines.disk.delete', 'machines.disk.migrate',
    'machines.network.add', 'machines.network.update', 'machines.network.delete',
    'machines.cloud_init.update', 'machines.credentials.inject',
    'machines.packages.manage', 'machines.tags.update', 'machines.metadata.update',
    'machines.rebuild', 'machines.rebuild.force', 'machines.clone', 'machines.migrate',
    'snapshots.create', 'snapshots.delete', 'snapshots.rollback',
    'backups.create', 'backups.restore',
})
