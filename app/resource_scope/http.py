"""HTTP composition for resource APIs; identity administration stays global."""
from typing import Annotated
from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from app.database import get_db
from app.models import ManagedVM
from app.security.core import authenticate
from app.tenancy.authorization import Principal, fail, identity as load_identity
from app.resource_scope.authorization import DEFAULT_SCOPE, permissions_for_identity, requested_scope
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
        from app.iam.service import authorize, request_context, scope_from_resource_scope
        decision = authorize(
            db,
            actor,
            permission,
            scope=scope_from_resource_scope(scope),
            context=request_context(request),
            write=request.method not in {'GET', 'HEAD', 'OPTIONS'},
        )
        if decision.decision != 'ALLOW':
            from app.security.core import audit
            audit(
                db,
                request,
                'authorization.denied',
                permission,
                result='denied',
                scope=scope_from_resource_scope(scope),
                details={
                    'required_permission': permission,
                    'decision': decision.decision,
                    'reason': decision.reason,
                },
            )
            db.commit()
            status = 409 if decision.decision == 'REQUIRES_APPROVAL' else 403
            error = 'approval_required' if status == 409 else 'permission_denied'
            raise HTTPException(status, {
                'error': error,
                'required_permission': permission,
                'reason': decision.reason,
                'request_id': request.state.request_id,
            })
        current_identity = load_identity(db, Principal.from_token(actor))
        from app.resource_scope.permissions import RESOURCE_PERMISSIONS
        try:
            legacy_effective = permissions_for_identity(
                db,
                current_identity,
                scope,
                write=request.method not in {'GET', 'HEAD', 'OPTIONS'},
            )
        except HTTPException:
            legacy_effective = frozenset()
        request.state.permissions = (
            (set(current_identity.global_permissions) - RESOURCE_PERMISSIONS)
            | set(legacy_effective)
            | {permission}
        )
        if decision.break_glass:
            request.state.break_glass_id = next(
                (item.get('id') for item in decision.assignments
                 if item.get('source') == 'break_glass'),
                None,
            )
        request.state.resource_scope = scope
        bind_scope(db, scope)
        from app.resource_scope.permissions import EXECUTION_PERMISSIONS
        from app.resource_scope.authorization import ensure_execution_ready
        if permission in EXECUTION_PERMISSIONS and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            ensure_execution_ready(db, current_identity, scope)
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
