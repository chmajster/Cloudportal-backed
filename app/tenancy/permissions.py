"""Permission declarations shared with the existing RBAC catalog."""

from app.projects.permissions import PROJECT_PERMISSION_ACTIONS
from app.resource_scope.permissions import RESOURCE_PERMISSIONS, SCOPE_PERMISSION_ACTIONS

TENANCY_PERMISSION_ACTIONS = {
    **PROJECT_PERMISSION_ACTIONS,
    **SCOPE_PERMISSION_ACTIONS,
    'tenants': 'read create update delete admin members.read members.manage roles.assign audit.read'.split(),
}
TENANCY_PERMISSIONS = frozenset(
    f'{area}.{action}' for area, actions in TENANCY_PERMISSION_ACTIONS.items() for action in actions
)
# These are platform operations. Even a role with the same name cannot delegate
# them inside a tenant. Authorization always checks permission identifiers.
PLATFORM_ONLY_PERMISSIONS = frozenset({'tenants.admin', 'tenants.create', 'tenants.delete', 'governance.admin', 'governance.access.read', 'governance.access.manage'})
DELEGABLE_PERMISSIONS = (TENANCY_PERMISSIONS | RESOURCE_PERMISSIONS) - PLATFORM_ONLY_PERMISSIONS
TENANCY_DEFAULT_ROLES = {
    'Tenant Administrator': DELEGABLE_PERMISSIONS,
    'Tenant Viewer': frozenset({'tenants.read', 'tenants.members.read', 'tenants.audit.read'}),
}
DEFAULT_TENANT_ID = '00000000-0000-0000-0000-000000000001'
