"""Project permission identifiers; role labels are never authorization checks."""
PROJECT_PERMISSION_ACTIONS = {
    'projects': 'read create update delete admin use members.read members.manage roles.assign audit.read select'.split(),
}
PROJECT_PERMISSIONS = frozenset('projects.' + action for action in PROJECT_PERMISSION_ACTIONS['projects'])
# Creation and tenant-wide/global crossing authority cannot be granted inside a project.
from app.resource_scope.permissions import RESOURCE_PERMISSIONS

PROJECT_DELEGABLE_PERMISSIONS = (PROJECT_PERMISSIONS | RESOURCE_PERMISSIONS) - {'projects.create', 'projects.admin', 'quotas.tenant.manage'}
PROJECT_DEFAULT_ROLES = {
    'Project Administrator': PROJECT_DELEGABLE_PERMISSIONS,
    'Project Viewer': frozenset({'projects.read', 'projects.members.read', 'projects.audit.read', 'projects.select'}),
    'Project Operator': frozenset({'projects.read', 'projects.use', 'projects.audit.read'}),
}
DEFAULT_PROJECT_ID = '00000000-0000-0000-0000-000000000002'
