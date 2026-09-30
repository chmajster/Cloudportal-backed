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
    return require_any(permission)


def require_any(*permissions):
    """Authorize any one permission while preserving resource-scope binding.

    This is intentionally an OR check. It allows domains to introduce
    granular permissions while keeping an older aggregate permission as a
    backwards-compatible alias during migration.
    """
    required = tuple(dict.fromkeys(str(value) for value in permissions if value))
    if not required:
        raise ValueError("At least one permission is required")

    def dependency(request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function'),
                   scope=Depends(resolve_http_scope)):
        from app.iam.service import authorize, request_context, scope_from_resource_scope
        decisions = []
        granted_permission = None
        granted_decision = None
        from app.policy_engine.entities import entity_role_allows_permission
        permitted_required = tuple(
            permission for permission in required
            if not getattr(scope, 'entity_role', None)
            or entity_role_allows_permission(scope.entity_role, permission)
        )
        if not permitted_required:
            from app.security.core import audit
            audit(
                db,
                request,
                'authorization.denied',
                required[0],
                result='denied',
                scope=scope_from_resource_scope(scope),
                details={
                    'required_permissions_any': list(required),
                    'entity': getattr(scope, 'entity_key', None),
                    'entity_role': getattr(scope, 'entity_role', None),
                    'reason': 'Selected Entity role does not permit this operation',
                },
            )
            db.commit()
            raise HTTPException(403, {
                'error': 'entity_role_denied',
                'entity': getattr(scope, 'entity_key', None),
                'entity_role': getattr(scope, 'entity_role', None),
                'required_permissions_any': list(required),
                'reason': 'Selected Entity role does not permit this operation',
                'request_id': request.state.request_id,
            })
        for permission in permitted_required:
            decision = authorize(
                db,
                actor,
                permission,
                scope=scope_from_resource_scope(scope),
                context=request_context(request),
                write=request.method not in {'GET', 'HEAD', 'OPTIONS'},
            )
            decisions.append((permission, decision))
            if decision.decision == 'ALLOW':
                granted_permission = permission
                granted_decision = decision
                break

        if granted_permission is None:
            from app.security.core import audit
            approval = next(
                ((permission, decision) for permission, decision in decisions
                 if decision.decision == 'REQUIRES_APPROVAL'),
                None,
            )
            permission, decision = approval or decisions[0]
            audit(
                db,
                request,
                'authorization.denied',
                permission,
                result='denied',
                scope=scope_from_resource_scope(scope),
                details={
                    'required_permissions_any': list(required),
                    'decision': decision.decision,
                    'reason': decision.reason,
                },
            )
            db.commit()
            status = 409 if approval else 403
            error = 'approval_required' if status == 409 else 'permission_denied'
            raise HTTPException(status, {
                'error': error,
                'required_permissions_any': list(required),
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
        effective_permissions = (
            (set(current_identity.global_permissions) - RESOURCE_PERMISSIONS)
            | set(legacy_effective)
            | {granted_permission}
        )
        if getattr(scope, 'entity_role', None):
            from app.policy_engine.entities import filter_permissions_for_entity_role
            effective_permissions = filter_permissions_for_entity_role(
                scope.entity_role, effective_permissions
            )
            # The permission that passed IAM must also survive the Entity ceiling.
            if granted_permission not in effective_permissions:
                raise HTTPException(403, {
                    'error': 'entity_role_denied',
                    'entity': scope.entity_key,
                    'entity_role': scope.entity_role,
                    'required_permission': granted_permission,
                    'request_id': request.state.request_id,
                })
        request.state.permissions = effective_permissions
        request.state.entity = getattr(scope, 'entity_key', None)
        if granted_decision.break_glass:
            request.state.break_glass_id = next(
                (item.get('id') for item in granted_decision.assignments
                 if item.get('source') == 'break_glass'),
                None,
            )
        request.state.resource_scope = scope
        bind_scope(db, scope)
        from app.resource_scope.permissions import EXECUTION_PERMISSIONS
        from app.resource_scope.authorization import ensure_execution_ready
        if granted_permission in EXECUTION_PERMISSIONS and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
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
