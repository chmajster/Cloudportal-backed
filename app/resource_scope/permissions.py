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
    'policies': 'read manage simulate audit exception.manage',
    # pro­mox_admin.scope.all is intentionally global-only and therefore omitted here.
    # A project role can receive only permissions that remain confined to the selected scope.
    'proxmox_admin': (
        'view dashboard.view providers.view nodes.view nodes.services.manage '
        'vm.view vm.power vm.modify vm.clone vm.migrate vm.delete '
        'containers.view containers.power containers.modify containers.clone containers.migrate containers.delete '
        'snapshots.view snapshots.manage storage.view storage.manage images.view images.manage '
        'templates.view templates.manage backups.view backups.run backups.restore backups.delete '
        'cluster.view tasks.view firewall.view firewall.manage console.use search bulk'
    ),
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
    'proxmox_admin.nodes.services.manage',
    'proxmox_admin.vm.power', 'proxmox_admin.vm.modify', 'proxmox_admin.vm.clone',
    'proxmox_admin.vm.migrate', 'proxmox_admin.vm.delete',
    'proxmox_admin.containers.power', 'proxmox_admin.containers.modify', 'proxmox_admin.containers.clone',
    'proxmox_admin.containers.migrate', 'proxmox_admin.containers.delete',
    'proxmox_admin.snapshots.manage', 'proxmox_admin.storage.manage',
    'proxmox_admin.images.manage', 'proxmox_admin.templates.manage',
    'proxmox_admin.backups.run', 'proxmox_admin.backups.restore', 'proxmox_admin.backups.delete',
    'proxmox_admin.firewall.manage', 'proxmox_admin.bulk',
})
