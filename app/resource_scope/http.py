"""HTTP composition for resource APIs; identity administration stays global."""
from typing import Annotated
from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from app.database import get_db
from app.models import ManagedVM
from app.security.core import authenticate
from app.tenancy.authorization import Principal, fail
from app.resource_scope.authorization import DEFAULT_SCOPE, authorize, requested_scope
from app.resource_scope.database import bind_scope


def resolve_http_scope(
    request: Request,
    tenant_header: Annotated[str | None, Header(alias='X-Tenant-ID', description='Selected tenant UUID; pair with X-Project-ID')] = None,
    project_header: Annotated[str | None, Header(alias='X-Project-ID', description='Selected project UUID; verified by the backend')] = None,
    tenant_id: Annotated[str | None, Query(description='Alternative to X-Tenant-ID; values must agree')] = None,
    project_id: Annotated[str | None, Query(description='Alternative to X-Project-ID; values must agree')] = None,
):
    # Read the raw multi-value input to reject repeated/contradicting fields.
    return requested_scope(request)


def require(permission):
    def dependency(request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function'),
                   scope=Depends(resolve_http_scope)):
        identity, effective = authorize(db, Principal.from_token(actor), scope, permission,
                                        write=request.method not in {'GET', 'HEAD', 'OPTIONS'})
        # Preserve platform permissions only if already global. Resource-scoped
        # grants do not flow into /auth/me or administration APIs.
        from app.resource_scope.permissions import RESOURCE_PERMISSIONS
        request.state.permissions = (set(identity.global_permissions) - RESOURCE_PERMISSIONS) | set(effective)
        request.state.resource_scope = scope
        bind_scope(db, scope)
        from app.resource_scope.permissions import EXECUTION_PERMISSIONS
        from app.resource_scope.authorization import ensure_execution_ready
        if permission in EXECUTION_PERMISSIONS and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            ensure_execution_ready(db, identity, scope)
        raw_provider_guard(db, request, scope)
        return actor
    return dependency


def raw_provider_guard(db, request, scope):
    """Provider responses and synchronous actions need more than ORM filtering."""
    path = request.url.path
    if not path.startswith('/api/v1/providers/') or not any(
            part in path for part in ('/vms/', '/tasks/', '/restore/')):
        return
    if scope != DEFAULT_SCOPE:
        fail(409, 'GOVERNED_DAY2_REQUIRED', 'Use the deployment job workflow for this project')
    from app.resource_scope.service import guard_raw_provider, guard_vm_identity
    try:
        provider_id = int(request.path_params['provider_id'])
    except (ValueError, TypeError, KeyError):
        fail(422, 'INVALID_PROVIDER', 'Provider identifier must be an integer')
    # A raw provider task/restore can touch identities absent from inventory,
    # including a VM between clone and inventory reconciliation. Once this
    # provider hosts another project, reject the legacy raw surface entirely.
    guard_raw_provider(db, provider_id, scope)
    vmid = request.path_params.get('vmid')
    if vmid is not None:
        guard_vm_identity(db, provider_id, vmid, scope)
