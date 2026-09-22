"""Only resource operations are delegable. Platform configuration remains global."""
RESOURCE_ACTIONS = {
    'deployments': 'read read_all create destroy adopt',
    'jobs': 'read read_all execute cancel',
    'terraform': 'read execute', 'ansible': 'read execute',
    'inventory': 'read read_all import update delete',
    'blueprints': 'read create update delete execute approve',
    'hostnames': 'read create update delete reserve release',
    'vms': 'read read_all manage_all power update delete clone migrate template console',
    'snapshots': 'read create delete rollback', 'backups': 'read create restore',
    'ipam': 'read create update delete allocate release',
    'schedules': 'read create update delete',
    'providers': 'read', 'credentials': 'read test',
    'quotas': 'read manage tenant.manage',
}
RESOURCE_PERMISSIONS = frozenset(f'{area}.{action}' for area, actions in RESOURCE_ACTIONS.items()
                                 for action in actions.split())
SCOPE_PERMISSION_ACTIONS = {'governance': ['admin', 'access.read', 'access.manage']}

# Request acceptance is not proof that arbitrary Terraform/Ansible/provider
# targets are confined to the selected project. These surfaces remain gated
# while the full governed provisioning/Day-2 adapters are being integrated.
EXECUTION_PERMISSIONS = frozenset({
    'deployments.create', 'deployments.destroy', 'deployments.adopt', 'jobs.execute',
    'terraform.execute', 'ansible.execute', 'blueprints.execute',
    'inventory.import', 'inventory.update', 'inventory.delete',
    'vms.power', 'vms.update', 'vms.delete', 'vms.clone', 'vms.migrate', 'vms.template',
    'snapshots.create', 'snapshots.delete', 'snapshots.rollback', 'backups.create', 'backups.restore',
})
