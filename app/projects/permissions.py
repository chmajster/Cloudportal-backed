"""Project permissions; identifiers, never role names, authorize operations."""
PROJECT_PERMISSION_ACTIONS = {
    'projects': 'read create update delete admin members.read members.manage roles.assign audit.read select'.split(),
}
PROJECT_PERMISSIONS = frozenset(
    f'{area}.{action}' for area, actions in PROJECT_PERMISSION_ACTIONS.items() for action in actions
)
# Creation/cross-project administration belongs to the containing tenant.
PROJECT_DELEGABLE_PERMISSIONS = PROJECT_PERMISSIONS - {'projects.create', 'projects.admin'}
PROJECT_DEFAULT_ROLES = {
    'Project Administrator': PROJECT_DELEGABLE_PERMISSIONS,
    'Project Viewer': frozenset({'projects.read', 'projects.select', 'projects.audit.read'}),
}
DEFAULT_PROJECT_ID = '00000000-0000-0000-0000-000000000002'
