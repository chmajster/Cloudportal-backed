from __future__ import annotations

from collections import defaultdict
from datetime import timedelta, timezone
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select

from app.database import get_db
from app.models import (
    Blueprint, Deployment, ManagedResource, ManagedVM, Permission, Role,
    RolePermission, Token, User, UserRole, now,
)
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment
from app.security.core import audit, authenticate
from app.rbac.service import ALL_PERMISSIONS, SYSTEM_ROLE_NAMES, ensure_admin_remains, permissions_from_names
from app.iam.models import (
    BreakGlassAccess, Group, GroupMember, JITAccessRequest, RoleAssignment,
    RoleConflict, RoleProfile, SCOPE_TYPES,
)
from app.iam.schemas import (
    AccessReviewOutput, AssignmentCreate, AssignmentOutput, AssignmentPage,
    AssignmentUpdate, AuthorizationDecisionOutput, AuthorizationInput,
    BreakGlassOutput, BreakGlassStart, BulkAssignmentCreate, BulkAssignmentResult,
    EffectiveAccessOutput, GroupCreate, GroupMemberInput, GroupMemberOutput,
    GroupMemberPage, GroupOutput, GroupPage, GroupUpdate, ImpactOutput,
    JITDecisionInput, JITRequestInput, JITRequestOutput, JITRequestPage,
    MeAccessOutput, PermissionCatalog, PermissionDescriptor, RolePage,
    RoleProfileInput, RoleProfileOutput, RoleProfileUpdate,
)
from app.iam.service import (
    PermissionMatcher, assignment_status, authorize, authorize_or_raise,
    effective_permissions, is_system_administrator, normalize_scope,
    permission_patterns_for_role, request_context, validate_assignment_conditions,
    validate_permission_patterns,
)
from app.vm_classification import tenant_vm_classification_settings


router = APIRouter(tags=['iam-rbac'])


def _naive(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _role(db, role_id: int) -> Role:
    row = db.get(Role, role_id)
    if row is None:
        raise HTTPException(404, {'error': 'role_not_found'})
    return row


def _user(db, user_id: int) -> User:
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, {'error': 'user_not_found'})
    return row


def _profile(db, role: Role, *, create=False, actor_id=None) -> RoleProfile | None:
    row = db.get(RoleProfile, role.id)
    if row is None and create:
        row = RoleProfile(
            role_id=role.id,
            description='',
            scope_types=list(SCOPE_TYPES),
            permission_patterns=[],
            inheritance_enabled=True,
            is_system=role.name in SYSTEM_ROLE_NAMES,
            enabled=True,
            assignable_by=[],
        )
        db.add(row)
        db.flush()
    return row


def _role_public(db, role: Role) -> dict:
    profile = _profile(db, role)
    count = db.scalar(
        select(func.count()).select_from(RoleAssignment).where(RoleAssignment.role_id == role.id)
    ) or 0
    return {
        'id': role.id,
        'name': role.name,
        'description': profile.description if profile else '',
        'permissions': sorted(p.name for p in role.permissions),
        'permission_patterns': sorted(profile.permission_patterns or []) if profile else [],
        'scope_types': list(profile.scope_types or SCOPE_TYPES) if profile else list(SCOPE_TYPES),
        'inheritance_enabled': profile.inheritance_enabled if profile else True,
        'system_role': profile.is_system if profile else role.name in SYSTEM_ROLE_NAMES,
        'enabled': profile.enabled if profile else True,
        'assignable_by': list(profile.assignable_by or []) if profile else [],
        'assignment_count': int(count),
    }


def _assignment_public(db, row: RoleAssignment) -> dict:
    role = db.get(Role, row.role_id)
    return {
        'id': row.id,
        'subject_type': row.subject_type,
        'subject_id': row.subject_id,
        'role_id': row.role_id,
        'role_name': role.name if role else f'role:{row.role_id}',
        'effect': row.effect,
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'organization_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
        'conditions': row.conditions or {},
        'permission_ceiling': row.permission_ceiling,
        'inherit': row.inherit,
        'approval_required': row.approval_required,
        'valid_from': row.valid_from,
        'valid_until': row.valid_until,
        'enabled': row.enabled,
        'source': row.source,
        'source_ref': row.source_ref,
        'source_group': db.get(Group, row.subject_id).name if row.subject_type == 'GROUP' and db.get(Group, row.subject_id) else None,
        'status': assignment_status(row),
        'created_by': row.created_by,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def _group_public(db, row: Group) -> dict:
    count = db.scalar(
        select(func.count()).select_from(GroupMember).where(GroupMember.group_id == row.id)
    ) or 0
    return {
        'id': row.id,
        'name': row.name,
        'description': row.description,
        'external_source': row.external_source,
        'external_id': row.external_id,
        'system_key': row.system_key,
        'managed_type': row.managed_type,
        'enabled': row.enabled,
        'member_count': int(count),
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def _break_glass_public(row: BreakGlassAccess) -> dict:
    return {
        'id': row.id,
        'user_id': row.user_id,
        'reason': row.reason,
        'valid_from': row.valid_from,
        'valid_until': row.valid_until,
        'enabled': row.enabled,
        'created_by': row.created_by,
        'ended_by': row.ended_by,
        'ended_at': row.ended_at,
    }


def _jit_public(db, row: JITAccessRequest) -> dict:
    role = db.get(Role, row.role_id)
    return {
        'id': row.id,
        'requester_id': row.requester_id,
        'role_id': row.role_id,
        'role_name': role.name if role else f'role:{row.role_id}',
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
        'requested_duration_minutes': row.requested_duration_minutes,
        'reason': row.reason,
        'status': row.status,
        'approval_id': row.approval_id,
        'assignment_id': row.assignment_id,
        'decided_by': row.decided_by,
        'decided_at': row.decided_at,
        'created_at': row.created_at,
    }


def _scope_dict(data) -> dict:
    raw = data.model_dump() if hasattr(data, 'model_dump') else dict(data)
    keys = {
        'scope_type', 'scope_id', 'tenant_id', 'organization_id', 'project_id',
        'apmid', 'environment', 'resource_pool_id', 'blueprint_id',
        'deployment_id', 'resource_id', 'machine_id',
    }
    return normalize_scope({key: raw.get(key) for key in keys if key in raw})


def _hydrate_scope(db, scope: dict) -> dict:
    """Resolve exact resource scopes to immutable tenant/project ownership."""
    kind = scope['scope_type']
    scope_id = scope.get('scope_id')
    row = None
    if kind == 'BLUEPRINT' and scope_id:
        try:
            row = db.get(Blueprint, int(scope_id))
        except (TypeError, ValueError):
            row = None
    elif kind == 'DEPLOYMENT' and scope_id:
        row = db.get(Deployment, scope_id)
    elif kind == 'RESOURCE' and scope_id:
        row = db.get(ManagedResource, scope_id)
    elif kind == 'MACHINE' and scope_id:
        row = db.get(ManagedVM, scope_id)

    if kind in {'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE'}:
        if row is None:
            raise HTTPException(404, {'error': 'resource_not_found'})
        row_tenant = str(row.tenant_id)
        row_project = str(row.project_id)
        if scope.get('tenant_id') and scope['tenant_id'] != row_tenant:
            raise HTTPException(404, {'error': 'resource_not_found'})
        if scope.get('project_id') and scope['project_id'] != row_project:
            raise HTTPException(404, {'error': 'resource_not_found'})
        scope['tenant_id'] = row_tenant
        scope['project_id'] = row_project
        if not scope.get('scope_id'):
            scope['scope_id'] = str(row.id)

    if kind == 'ORGANIZATION':
        scope['tenant_id'] = scope.get('tenant_id') or scope.get('scope_id')
    if kind == 'PROJECT':
        scope['project_id'] = scope.get('project_id') or scope.get('scope_id')

    if kind != 'GLOBAL':
        if kind != 'ORGANIZATION' and not scope.get('project_id'):
            raise HTTPException(422, {
                'error': 'project_scope_required',
                'message': f'{kind} assignments require project_id',
            })
        if not scope.get('tenant_id'):
            if scope.get('project_id'):
                project = db.get(Project, scope['project_id'])
                if project is None or project.deleted_at is not None:
                    raise HTTPException(404, {'error': 'resource_not_found'})
                scope['tenant_id'] = str(project.tenant_id)
            else:
                raise HTTPException(422, {'error': 'organization_scope_required'})

    if scope.get('tenant_id'):
        tenant = db.get(Tenant, scope['tenant_id'])
        if tenant is None or tenant.deleted_at is not None:
            raise HTTPException(404, {'error': 'resource_not_found'})
    if scope.get('project_id'):
        project = db.get(Project, scope['project_id'])
        if (
            project is None
            or project.deleted_at is not None
            or str(project.tenant_id) != str(scope['tenant_id'])
        ):
            raise HTTPException(404, {'error': 'resource_not_found'})
    return scope


def _scope_management_actions(scope: dict, operation: str = 'create') -> tuple[str, ...]:
    iam_actions = {
        'create': ('iam.assign', 'iam.binding.create'),
        'update': ('iam.binding.update',),
        'delete': ('iam.binding.delete',),
    }.get(operation, ())
    if scope['scope_type'] == 'GLOBAL':
        return (*iam_actions, 'rbac.assignments.manage', 'roles.assign')
    if scope['scope_type'] == 'ORGANIZATION':
        return (*iam_actions, 'rbac.assignments.manage', 'tenants.roles.assign', 'organizations.assign_roles')
    return (*iam_actions, 'rbac.assignments.manage', 'projects.roles.assign')


def _require_any(
    db,
    actor,
    actions: Iterable[str],
    scope: dict,
    request: Request,
    *,
    write: bool | None = None,
):
    if is_system_administrator(db, actor):
        return None
    decisions = []
    write_operation = request.method not in {'GET', 'HEAD', 'OPTIONS'} if write is None else write
    for action in actions:
        if action not in ALL_PERMISSIONS:
            continue
        decision = authorize(
            db, actor, action, scope=scope, context=request_context(request),
            write=write_operation,
        )
        decisions.append(decision)
        if decision.allowed:
            if decision.break_glass:
                request.state.break_glass_id = next(
                    (a.get('id') for a in decision.assignments if a.get('source') == 'break_glass'),
                    None,
                )
            return decision
    detail = {
        'error': 'permission_denied',
        'required_permission': list(actions),
        'request_id': request.state.request_id,
    }
    if decisions:
        detail['reason'] = decisions[-1].reason
    raise HTTPException(403, detail)


def _expanded_role_permissions(db, role: Role) -> set[str]:
    result = {p.name for p in role.permissions}
    for pattern in permission_patterns_for_role(db, role.id):
        result.update(permission for permission in ALL_PERMISSIONS if PermissionMatcher.matches(pattern, permission))
    return result


def _assert_role_delegation_boundary(
    db, actor, role: Role, scope: dict, request: Request, *, operation: str = 'create'
):
    system_admin = is_system_administrator(db, actor)
    if system_admin and operation in {'update', 'delete'}:
        return
    profile = _profile(db, role)
    if profile is not None and not profile.enabled:
        raise HTTPException(409, {'error': 'role_disabled'})
    if profile is not None and profile.scope_types and scope['scope_type'] not in set(profile.scope_types):
        raise HTTPException(422, {
            'error': 'role_scope_not_allowed',
            'scope_type': scope['scope_type'],
        })
    if system_admin:
        return
    missing = []
    for permission in sorted(_expanded_role_permissions(db, role)):
        decision = authorize(
            db, actor, permission, scope=scope, context=request_context(request), write=False,
        )
        if decision.decision not in {'ALLOW', 'REQUIRES_APPROVAL'}:
            missing.append(permission)
            if len(missing) >= 20:
                break
    if missing:
        raise HTTPException(403, {
            'error': 'delegation_boundary_exceeded',
            'missing_permissions': missing,
            'request_id': request.state.request_id,
        })


def _assert_delegatable(
    db, actor, role: Role, scope: dict, request: Request, *, operation: str = 'create'
):
    _require_any(db, actor, _scope_management_actions(scope, operation), scope, request)
    _assert_role_delegation_boundary(
        db, actor, role, scope, request, operation=operation,
    )


def _validate_subject(db, subject_type: str, subject_id: str):
    if subject_type in {'USER', 'SERVICE_ACCOUNT'}:
        try:
            user = _user(db, int(subject_id))
        except (TypeError, ValueError):
            raise HTTPException(422, {'error': 'invalid_subject_id'}) from None
        if subject_type == 'SERVICE_ACCOUNT' and not user.is_service_account:
            raise HTTPException(422, {'error': 'subject_is_not_service_account'})
        return
    if subject_type == 'GROUP':
        group = db.get(Group, subject_id)
        if group is None or not group.enabled:
            raise HTTPException(404, {'error': 'group_not_found'})
        return
    if subject_type == 'API_TOKEN':
        try:
            token = db.get(Token, int(subject_id))
        except (TypeError, ValueError):
            token = None
        if token is None or token.kind != 'api' or token.revoked_at is not None:
            raise HTTPException(404, {'error': 'token_not_found'})


def _assert_no_role_conflict(
    db,
    subject_type: str,
    subject_id: str,
    role_id: int,
    scope: dict,
    *,
    exclude_assignment_id: str | None = None,
):
    conflicts = db.scalars(
        select(RoleConflict).where(
            RoleConflict.enabled.is_(True),
            RoleConflict.severity == 'BLOCK',
            or_(RoleConflict.role_a_id == role_id, RoleConflict.role_b_id == role_id),
            or_(RoleConflict.scope_type == 'GLOBAL', RoleConflict.scope_type == scope['scope_type']),
        )
    ).all()
    if not conflicts:
        return
    conflicting_ids = {
        row.role_b_id if row.role_a_id == role_id else row.role_a_id for row in conflicts
    }
    query = select(RoleAssignment.id).where(
        RoleAssignment.subject_type == subject_type,
        RoleAssignment.subject_id == subject_id,
        RoleAssignment.role_id.in_(conflicting_ids),
        RoleAssignment.enabled.is_(True),
        RoleAssignment.scope_type == scope['scope_type'],
        or_(RoleAssignment.scope_id == scope.get('scope_id'), RoleAssignment.scope_id.is_(None)),
        or_(RoleAssignment.project_id == scope.get('project_id'), RoleAssignment.project_id.is_(None)),
    )
    if exclude_assignment_id is not None:
        query = query.where(RoleAssignment.id != exclude_assignment_id)
    if db.scalar(query):
        raise HTTPException(409, {'error': 'separation_of_duties_conflict'})


def _new_assignment(db, actor, data: AssignmentCreate, scope: dict, role: Role) -> RoleAssignment:
    row = RoleAssignment(
        subject_type=data.subject_type,
        subject_id=data.subject_id,
        role_id=data.role_id,
        effect=data.effect,
        scope_type=scope['scope_type'],
        scope_id=scope.get('scope_id'),
        tenant_id=scope.get('tenant_id'),
        project_id=scope.get('project_id'),
        apmid=scope.get('apmid'),
        environment=scope.get('environment'),
        conditions=data.conditions,
        permission_ceiling=sorted(_expanded_role_permissions(db, role)),
        inherit=data.inherit,
        approval_required=data.approval_required,
        valid_from=_naive(data.valid_from),
        valid_until=_naive(data.valid_until),
        enabled=data.enabled,
        source=data.source,
        source_ref=data.source_ref,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    return row


def _assignments_for_user(db, user_id: int) -> list[RoleAssignment]:
    group_ids = list(db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id)))
    clauses = [
        and_(RoleAssignment.subject_type.in_(('USER', 'SERVICE_ACCOUNT')),
             RoleAssignment.subject_id == str(user_id)),
    ]
    if group_ids:
        clauses.append(and_(
            RoleAssignment.subject_type == 'GROUP',
            RoleAssignment.subject_id.in_(group_ids),
        ))
    return list(db.scalars(
        select(RoleAssignment).where(or_(*clauses)).order_by(RoleAssignment.created_at.desc())
    ))


def _iam_catalog_scope() -> dict:
    return {'scope_type': 'GLOBAL'}


def _subject_user_public(row: User) -> dict:
    display_name = ' '.join(
        part for part in (row.first_name, row.last_name) if part
    ).strip() or row.username
    return {
        'id': row.id,
        'username': row.username,
        'email': row.email,
        'first_name': row.first_name,
        'last_name': row.last_name,
        'display_name': display_name,
        'is_active': row.is_active,
        'is_locked': row.is_locked,
        'is_service_account': row.is_service_account,
    }


@router.get('/iam/subjects')
def iam_subjects(
    request: Request,
    type: str = Query('USER'),
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    kind = str(type or 'USER').upper()
    if kind not in {'USER', 'GROUP', 'SERVICE_ACCOUNT', 'API_TOKEN'}:
        raise HTTPException(422, {'error': 'unsupported_subject_type', 'subject_type': kind})

    legacy_permission = 'groups.read' if kind == 'GROUP' else (
        'tokens.read' if kind == 'API_TOKEN' else 'users.read'
    )
    _require_any(
        db, actor, ('iam.subject.read', legacy_permission),
        _iam_catalog_scope(), request,
    )

    if kind in {'USER', 'SERVICE_ACCOUNT'}:
        service_account = kind == 'SERVICE_ACCOUNT'
        filters = (
            User.is_active.is_(True),
            User.is_service_account.is_(service_account),
        )
        total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
        rows = db.scalars(
            select(User).where(*filters)
            .order_by(User.username, User.id).offset(offset).limit(limit)
        ).all()
        return {
            'items': [_subject_user_public(row) for row in rows],
            'total': int(total), 'limit': limit, 'offset': offset,
        }

    if kind == 'GROUP':
        filters = (Group.enabled.is_(True),)
        total = db.scalar(select(func.count()).select_from(Group).where(*filters)) or 0
        rows = db.scalars(
            select(Group).where(*filters)
            .order_by(Group.name, Group.id).offset(offset).limit(limit)
        ).all()
        return {
            'items': [_group_public(db, row) for row in rows],
            'total': int(total), 'limit': limit, 'offset': offset,
        }

    filters = (
        Token.kind == 'api',
        Token.revoked_at.is_(None),
    )
    total = db.scalar(select(func.count()).select_from(Token).where(*filters)) or 0
    rows = db.scalars(
        select(Token).where(*filters)
        .order_by(Token.name, Token.id).offset(offset).limit(limit)
    ).all()
    return {
        'items': [{
            'id': row.id,
            'name': row.name,
            'token_prefix': row.token_prefix,
            'user_id': row.user_id,
        } for row in rows],
        'total': int(total), 'limit': limit, 'offset': offset,
    }


@router.get('/iam/organizations')
@router.get('/organizations')
def iam_organizations(
    request: Request,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _require_any(
        db, actor, ('organizations.read', 'tenants.read', 'iam.assign'),
        _iam_catalog_scope(), request,
    )
    filters = (Tenant.deleted_at.is_(None),)
    total = db.scalar(select(func.count()).select_from(Tenant).where(*filters)) or 0
    rows = db.scalars(
        select(Tenant).where(*filters)
        .order_by(Tenant.name, Tenant.id).offset(offset).limit(limit)
    ).all()
    return {
        'items': [{
            'id': row.id,
            'name': row.name,
            'slug': row.slug,
            'status': row.status,
        } for row in rows],
        'total': int(total), 'limit': limit, 'offset': offset,
    }


@router.get('/iam/projects')
def iam_projects(
    request: Request,
    organization_id: str | None = None,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _require_any(
        db, actor, ('projects.read', 'iam.assign'),
        _iam_catalog_scope(), request,
    )
    filters = [Project.deleted_at.is_(None)]
    if organization_id:
        filters.append(Project.tenant_id == organization_id)
    total = db.scalar(select(func.count()).select_from(Project).where(*filters)) or 0
    rows = db.scalars(
        select(Project).where(*filters)
        .order_by(Project.tenant_id, Project.name, Project.id)
        .offset(offset).limit(limit)
    ).all()
    return {
        'items': [{
            'id': row.id,
            'tenant_id': row.tenant_id,
            'organization_id': row.tenant_id,
            'name': row.name,
            'slug': row.slug,
            'status': row.status,
        } for row in rows],
        'total': int(total), 'limit': limit, 'offset': offset,
    }


def _classification_scope(db, organization_id: str, project_id: str) -> tuple[dict, dict]:
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': 'PROJECT',
        'scope_id': project_id,
        'tenant_id': organization_id,
        'project_id': project_id,
    }))
    return scope, tenant_vm_classification_settings(db, organization_id)


@router.get('/iam/apmids')
def iam_apmids(
    organization_id: str,
    project_id: str,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    scope, classification = _classification_scope(db, organization_id, project_id)
    _require_any(db, actor, ('projects.read', 'iam.assign'), scope, request)
    values = list(classification.get('apmids') or [])
    return {'items': [{'id': value, 'name': value} for value in values], 'total': len(values)}


@router.get('/iam/environments')
def iam_environments(
    organization_id: str,
    project_id: str,
    request: Request,
    apmid: str | None = None,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    scope, classification = _classification_scope(db, organization_id, project_id)
    _require_any(db, actor, ('projects.read', 'iam.assign'), scope, request)
    if apmid:
        values = list((classification.get('apmid_environments') or {}).get(apmid, []))
    else:
        values = [
            name for name, enabled in (classification.get('environments') or {}).items()
            if enabled
        ]
    return {
        'items': [{'id': value, 'name': str(value).upper()} for value in values],
        'total': len(values),
    }


@router.get('/rbac/permissions', response_model=PermissionCatalog)
def permission_catalog(
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    authorize_or_raise(db, actor, 'roles.read', scope={'scope_type': 'GLOBAL'})
    items = []
    modules = defaultdict(list)
    sensitive_markers = (
        '.delete', '.force', '.override', '.bypass', 'read_secret', '.impersonate',
        'break_glass.', 'roles.assign', 'assignments.manage',
    )
    for name in sorted(ALL_PERMISSIONS):
        module, _, action = name.partition('.')
        items.append({
            'name': name,
            'module': module,
            'action': action,
            'force_or_sensitive': any(marker in name for marker in sensitive_markers),
        })
        modules[module].append(name)
    return {'items': items, 'modules': dict(modules)}


@router.get('/rbac/roles', response_model=RolePage)
@router.get('/iam/roles', response_model=RolePage)
def roles(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    assignable: bool = Query(False),
    scope_type: str = Query('GLOBAL'),
    scope_id: str | None = Query(None),
    tenant_id: str | None = Query(None),
    project_id: str | None = Query(None),
    apmid: str | None = Query(None),
    environment: str | None = Query(None),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _require_any(
        db, actor, ('iam.roles.read', 'roles.read'),
        _iam_catalog_scope(), request,
    )
    if not assignable:
        total = db.scalar(select(func.count()).select_from(Role)) or 0
        rows = db.scalars(select(Role).order_by(Role.name).offset(offset).limit(limit)).all()
        return {
            'items': [_role_public(db, row) for row in rows],
            'total': int(total),
            'limit': limit,
            'offset': offset,
        }

    try:
        scope = _hydrate_scope(db, normalize_scope({
            'scope_type': scope_type,
            'scope_id': scope_id,
            'tenant_id': tenant_id,
            'project_id': project_id,
            'apmid': apmid,
            'environment': environment,
        }))
    except ValueError as exc:
        raise HTTPException(422, {'error': 'invalid_scope', 'message': str(exc)}) from exc

    _require_any(
        db,
        actor,
        _scope_management_actions(scope, 'create'),
        scope,
        request,
        write=True,
    )
    assignable_rows = []
    for role in db.scalars(select(Role).order_by(Role.name)).all():
        try:
            _assert_role_delegation_boundary(
                db, actor, role, scope, request, operation='create',
            )
        except HTTPException as exc:
            if exc.status_code in {403, 409, 422}:
                continue
            raise
        assignable_rows.append(role)

    total = len(assignable_rows)
    rows = assignable_rows[offset:offset + limit]
    return {
        'items': [_role_public(db, row) for row in rows],
        'total': total,
        'limit': limit,
        'offset': offset,
    }


@router.post('/rbac/roles', response_model=RoleProfileOutput, status_code=201)
def create_role(
    data: RoleProfileInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    authorize_or_raise(
        db, actor, 'roles.create', scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    if data.name in SYSTEM_ROLE_NAMES:
        raise HTTPException(409, {'error': 'system_role_name_reserved'})
    patterns = validate_permission_patterns(data.permission_patterns)
    role = Role(name=data.name, permissions=permissions_from_names(db, data.permissions))
    db.add(role)
    db.flush()
    profile = RoleProfile(
        role_id=role.id,
        description=data.description,
        scope_types=list(dict.fromkeys(data.scope_types)),
        permission_patterns=patterns,
        inheritance_enabled=data.inheritance_enabled,
        is_system=False,
        enabled=data.enabled,
        assignable_by=list(dict.fromkeys(data.assignable_by)),
    )
    db.add(profile)
    audit(db, request, 'role.created', 'roles', role.id)
    db.flush()
    return _role_public(db, role)


@router.patch('/rbac/roles/{role_id}', response_model=RoleProfileOutput)
def update_role(
    role_id: int,
    data: RoleProfileUpdate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    role = _role(db, role_id)
    authorize_or_raise(
        db, actor, 'roles.update', scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    profile = _profile(db, role, create=True, actor_id=actor.user_id)
    values = data.model_dump(exclude_unset=True)
    if 'name' in values:
        if profile.is_system and values['name'] != role.name:
            raise HTTPException(409, {'error': 'system_role_name_immutable'})
        role.name = values.pop('name')
    if 'permissions' in values:
        requested = set(values.pop('permissions'))
        missing = [
            permission for permission in sorted(requested)
            if not authorize(db, actor, permission, scope={'scope_type': 'GLOBAL'}).allowed
        ]
        if missing:
            raise HTTPException(403, {
                'error': 'privilege_escalation',
                'missing_permissions': missing[:20],
            })
        role.permissions = permissions_from_names(db, requested)
    if 'permission_patterns' in values:
        patterns = validate_permission_patterns(values.pop('permission_patterns'))
        expanded = {
            permission for pattern in patterns for permission in ALL_PERMISSIONS
            if PermissionMatcher.matches(pattern, permission)
        }
        missing = [
            permission for permission in sorted(expanded)
            if not authorize(db, actor, permission, scope={'scope_type': 'GLOBAL'}).allowed
        ]
        if missing:
            raise HTTPException(403, {
                'error': 'privilege_escalation',
                'missing_permissions': missing[:20],
            })
        profile.permission_patterns = patterns
    for field in ('description', 'scope_types', 'inheritance_enabled', 'enabled', 'assignable_by'):
        if field in values:
            setattr(profile, field, values[field])
    db.flush()
    ensure_admin_remains(db)
    audit(db, request, 'role.updated', 'roles', role.id)
    return _role_public(db, role)


@router.delete('/rbac/roles/{role_id}')
def delete_role(
    role_id: int,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    role = _role(db, role_id)
    authorize_or_raise(
        db, actor, 'roles.delete', scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    profile = _profile(db, role)
    if (profile and profile.is_system) or role.name in SYSTEM_ROLE_NAMES:
        raise HTTPException(409, {'error': 'system_role_protected'})
    if db.scalar(select(RoleAssignment.id).where(RoleAssignment.role_id == role_id)):
        raise HTTPException(409, {'error': 'role_in_use'})
    if db.scalar(select(UserRole.user_id).where(UserRole.role_id == role_id)):
        raise HTTPException(409, {'error': 'role_in_use'})
    if db.scalar(select(TenantRoleAssignment.id).where(TenantRoleAssignment.role_id == role_id)):
        raise HTTPException(409, {'error': 'role_in_use'})
    if db.scalar(select(ProjectRoleAssignment.id).where(ProjectRoleAssignment.role_id == role_id)):
        raise HTTPException(409, {'error': 'role_in_use'})
    db.delete(role)
    audit(db, request, 'role.deleted', 'roles', role_id)
    return {'deleted': True}


@router.get('/rbac/roles/{role_id}/impact', response_model=ImpactOutput)
def role_impact(
    role_id: int,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _role(db, role_id)
    authorize_or_raise(db, actor, 'rbac.impact.read', scope={'scope_type': 'GLOBAL'})
    user_subjects = list(db.scalars(select(RoleAssignment.subject_id).where(
        RoleAssignment.role_id == role_id,
        RoleAssignment.subject_type.in_(('USER', 'SERVICE_ACCOUNT')),
    )))
    group_subjects = list(db.scalars(select(RoleAssignment.subject_id).where(
        RoleAssignment.role_id == role_id,
        RoleAssignment.subject_type == 'GROUP',
    )))
    return {
        'role_id': role_id,
        'affected_users': sorted({int(value) for value in user_subjects if str(value).isdigit()}),
        'affected_groups': sorted(set(group_subjects)),
        'active_assignments': int(db.scalar(select(func.count()).select_from(RoleAssignment).where(
            RoleAssignment.role_id == role_id,
            RoleAssignment.enabled.is_(True),
        )) or 0),
        'legacy_global_users': sorted(set(db.scalars(
            select(UserRole.user_id).where(UserRole.role_id == role_id)
        ))),
        'legacy_tenant_assignments': int(db.scalar(
            select(func.count()).select_from(TenantRoleAssignment).where(
                TenantRoleAssignment.role_id == role_id
            )
        ) or 0),
        'legacy_project_assignments': int(db.scalar(
            select(func.count()).select_from(ProjectRoleAssignment).where(
                ProjectRoleAssignment.role_id == role_id
            )
        ) or 0),
    }


@router.get('/rbac/assignments', response_model=AssignmentPage)
def assignments(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    subject_type: str | None = None,
    subject_id: str | None = None,
    scope_type: str | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
    role_id: int | None = None,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _require_any(
        db, actor, ('iam.binding.read', 'rbac.assignments.read'),
        _iam_catalog_scope(), request,
    )
    filters = []
    for column, value in (
        (RoleAssignment.subject_type, subject_type),
        (RoleAssignment.subject_id, subject_id),
        (RoleAssignment.scope_type, scope_type),
        (RoleAssignment.tenant_id, tenant_id),
        (RoleAssignment.project_id, project_id),
        (RoleAssignment.role_id, role_id),
    ):
        if value is not None:
            filters.append(column == value)
    total = db.scalar(select(func.count()).select_from(RoleAssignment).where(*filters)) or 0
    rows = db.scalars(
        select(RoleAssignment).where(*filters)
        .order_by(RoleAssignment.created_at.desc())
        .offset(offset).limit(limit)
    ).all()
    return {
        'items': [_assignment_public(db, row) for row in rows],
        'total': int(total),
        'limit': limit,
        'offset': offset,
    }


@router.post('/rbac/assignments', response_model=AssignmentOutput, status_code=201)
def create_assignment(
    data: AssignmentCreate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    validate_assignment_conditions(data.conditions)
    _validate_subject(db, data.subject_type, data.subject_id)
    role = _role(db, data.role_id)
    scope = _hydrate_scope(db, _scope_dict(data))
    _assert_delegatable(db, actor, role, scope, request)
    _assert_no_role_conflict(db, data.subject_type, data.subject_id, data.role_id, scope)
    row = _new_assignment(db, actor, data, scope, role)
    audit(db, request, 'role_assignment.created', 'iam_role_assignments', row.id)
    return _assignment_public(db, row)


@router.post('/rbac/assignments/bulk', response_model=BulkAssignmentResult, status_code=201)
def create_assignments_bulk(
    data: BulkAssignmentCreate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    rows = []
    for item in data.assignments:
        validate_assignment_conditions(item.conditions)
        _validate_subject(db, item.subject_type, item.subject_id)
        role = _role(db, item.role_id)
        scope = _hydrate_scope(db, _scope_dict(item))
        _assert_delegatable(db, actor, role, scope, request)
        _assert_no_role_conflict(db, item.subject_type, item.subject_id, item.role_id, scope)
        rows.append(_new_assignment(db, actor, item, scope, role))
    audit(db, request, 'role_assignment.bulk_created', 'iam_role_assignments', str(len(rows)))
    return {'items': [_assignment_public(db, row) for row in rows], 'created': len(rows)}


@router.patch('/rbac/assignments/{assignment_id}', response_model=AssignmentOutput)
def update_assignment(
    assignment_id: str,
    data: AssignmentUpdate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(RoleAssignment, assignment_id)
    if row is None:
        raise HTTPException(404, {'error': 'assignment_not_found'})
    role = _role(db, row.role_id)
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
    }))
    _assert_delegatable(db, actor, role, scope, request, operation='update')
    values = data.model_dump(exclude_unset=True)
    if 'role_id' in values:
        if values['role_id'] is None:
            raise HTTPException(422, {'error': 'role_id_required'})
        next_role = _role(db, values['role_id'])
        _assert_delegatable(
            db, actor, next_role, scope, request, operation='update',
        )
        _assert_no_role_conflict(
            db,
            row.subject_type,
            row.subject_id,
            next_role.id,
            scope,
            exclude_assignment_id=row.id,
        )
        row.permission_ceiling = sorted(_expanded_role_permissions(db, next_role))
    if 'conditions' in values:
        validate_assignment_conditions(values['conditions'])
    if 'valid_from' in values:
        values['valid_from'] = _naive(values['valid_from'])
    if 'valid_until' in values:
        values['valid_until'] = _naive(values['valid_until'])
    final_from = values.get('valid_from', row.valid_from)
    final_until = values.get('valid_until', row.valid_until)
    if final_from and final_until and final_until <= final_from:
        raise HTTPException(422, {'error': 'invalid_validity_window'})
    for key, value in values.items():
        setattr(row, key, value)
    db.flush()
    if row.scope_type == 'GLOBAL':
        ensure_admin_remains(db)
    audit(db, request, 'role_assignment.updated', 'iam_role_assignments', row.id)
    return _assignment_public(db, row)


@router.delete('/rbac/assignments/{assignment_id}')
def delete_assignment(
    assignment_id: str,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(RoleAssignment, assignment_id)
    if row is None:
        raise HTTPException(404, {'error': 'assignment_not_found'})
    role = _role(db, row.role_id)
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
    }))
    _assert_delegatable(db, actor, role, scope, request, operation='delete')
    was_global = row.scope_type == 'GLOBAL'
    db.delete(row)
    db.flush()
    if was_global:
        ensure_admin_remains(db)
    audit(db, request, 'role_assignment.deleted', 'iam_role_assignments', assignment_id)
    return {'deleted': True}


@router.get('/rbac/groups', response_model=GroupPage)
def groups(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    authorize_or_raise(db, actor, 'groups.read', scope={'scope_type': 'GLOBAL'})
    total = db.scalar(select(func.count()).select_from(Group)) or 0
    rows = db.scalars(select(Group).order_by(Group.name).offset(offset).limit(limit)).all()
    return {
        'items': [_group_public(db, row) for row in rows],
        'total': int(total),
        'limit': limit,
        'offset': offset,
    }


@router.post('/rbac/groups', response_model=GroupOutput, status_code=201)
def create_group(
    data: GroupCreate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    permission = 'groups.map_identity' if data.external_source else 'groups.create'
    authorize_or_raise(
        db, actor, permission, scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    row = Group(**data.model_dump(), created_by=actor.user_id)
    db.add(row)
    db.flush()
    audit(db, request, 'group.created', 'iam_groups', row.id)
    return _group_public(db, row)


@router.patch('/rbac/groups/{group_id}', response_model=GroupOutput)
def update_group(
    group_id: str,
    data: GroupUpdate,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(Group, group_id)
    if row is None:
        raise HTTPException(404, {'error': 'group_not_found'})
    permission = 'groups.map_identity' if (
        data.external_source is not None or data.external_id is not None
    ) else 'groups.update'
    authorize_or_raise(
        db, actor, permission, scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.flush()
    ensure_admin_remains(db)
    audit(db, request, 'group.updated', 'iam_groups', row.id)
    return _group_public(db, row)


@router.delete('/rbac/groups/{group_id}')
def delete_group(
    group_id: str,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(Group, group_id)
    if row is None:
        raise HTTPException(404, {'error': 'group_not_found'})
    authorize_or_raise(
        db, actor, 'groups.delete', scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    if row.system_key:
        raise HTTPException(409, {'error': 'managed_group_protected'})
    db.delete(row)
    db.flush()
    ensure_admin_remains(db)
    audit(db, request, 'group.deleted', 'iam_groups', group_id)
    return {'deleted': True}


@router.get('/rbac/groups/{group_id}/members', response_model=GroupMemberPage)
def group_members(
    group_id: str,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(404, {'error': 'group_not_found'})
    authorize_or_raise(db, actor, 'groups.read', scope={'scope_type': 'GLOBAL'})
    rows = db.execute(
        select(GroupMember, User)
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.username)
    ).all()
    items = [{
        'user_id': member.user_id,
        'username': user.username,
        'email': user.email,
        'source': member.source,
        'external_id': member.external_id,
        'created_at': member.created_at,
    } for member, user in rows]
    return {'items': items, 'total': len(items)}


@router.post('/rbac/groups/{group_id}/members', response_model=GroupMemberOutput, status_code=201)
def add_group_member(
    group_id: str,
    data: GroupMemberInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(404, {'error': 'group_not_found'})
    permission = 'groups.map_identity' if data.source != 'LOCAL' else 'groups.manage_members'
    authorize_or_raise(
        db, actor, permission, scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    user = _user(db, data.user_id)
    existing = db.get(GroupMember, {'group_id': group_id, 'user_id': data.user_id})
    if existing is not None:
        raise HTTPException(409, {'error': 'group_member_exists'})
    member = GroupMember(
        group_id=group_id,
        user_id=data.user_id,
        source=data.source,
        external_id=data.external_id,
        created_by=actor.user_id,
    )
    db.add(member)
    db.flush()
    audit(db, request, 'user.added_to_group', 'iam_groups', f'{group_id}:{data.user_id}')
    return {
        'user_id': member.user_id,
        'username': user.username,
        'email': user.email,
        'source': member.source,
        'external_id': member.external_id,
        'created_at': member.created_at,
    }


@router.delete('/rbac/groups/{group_id}/members/{user_id}')
def remove_group_member(
    group_id: str,
    user_id: int,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(GroupMember, {'group_id': group_id, 'user_id': user_id})
    if row is None:
        raise HTTPException(404, {'error': 'group_member_not_found'})
    permission = 'groups.map_identity' if row.source != 'LOCAL' else 'groups.manage_members'
    authorize_or_raise(
        db, actor, permission, scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    db.delete(row)
    db.flush()
    ensure_admin_remains(db)
    audit(db, request, 'user.removed_from_group', 'iam_groups', f'{group_id}:{user_id}')
    return {'deleted': True}


def _authorize_input(db, actor, data: AuthorizationInput, request: Request, required_admin: str):
    authorize_or_raise(
        db, actor, required_admin, scope={'scope_type': 'GLOBAL'},
        context=request_context(request),
    )
    scope = _hydrate_scope(db, _scope_dict(data))
    return authorize(
        db, actor, data.action,
        scope=scope,
        resource=data.resource,
        context={**request_context(request), **data.context},
        subject_user_id=data.user_id,
    )


@router.post('/authorization/check', response_model=AuthorizationDecisionOutput)
def authorization_check(
    data: AuthorizationInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    return _authorize_input(db, actor, data, request, 'authorization.check').public()


@router.post('/authorization/explain', response_model=AuthorizationDecisionOutput)
def authorization_explain(
    data: AuthorizationInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    return _authorize_input(db, actor, data, request, 'authorization.explain').public()


@router.post('/authorization/simulate', response_model=AuthorizationDecisionOutput)
def authorization_simulate(
    data: AuthorizationInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    if data.user_id is None:
        data = data.model_copy(update={'user_id': actor.user_id})
    return _authorize_input(db, actor, data, request, 'authorization.simulate').public()


@router.get('/users/{user_id}/effective-access', response_model=EffectiveAccessOutput)
def user_effective_access(
    user_id: int,
    scope_type: str = Query('GLOBAL'),
    scope_id: str | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
    apmid: str | None = None,
    environment: str | None = None,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    authorize_or_raise(db, actor, 'authorization.explain', scope={'scope_type': 'GLOBAL'})
    _user(db, user_id)
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': scope_type,
        'scope_id': scope_id,
        'tenant_id': tenant_id,
        'project_id': project_id,
        'apmid': apmid,
        'environment': environment,
    }))
    permissions = effective_permissions(db, actor, scope=scope, subject_user_id=user_id)
    assignments = _assignments_for_user(db, user_id)
    role_ids = {row.role_id for row in assignments}
    roles = [
        {'id': role.id, 'name': role.name}
        for role in db.scalars(select(Role).where(Role.id.in_(role_ids)).order_by(Role.name))
    ] if role_ids else []
    return {
        'user_id': user_id,
        'scope': scope,
        'permissions': sorted(permissions),
        'roles': roles,
        'assignments': [_assignment_public(db, row) for row in assignments],
    }


@router.get('/me/access', response_model=MeAccessOutput)
def me_access(
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    organizations = db.execute(
        select(Tenant.id, Tenant.name, Tenant.slug, Tenant.status)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == actor.user_id,
            TenantMembership.status == 'active',
            Tenant.deleted_at.is_(None),
        )
        .order_by(Tenant.name)
    ).all()
    projects = db.execute(
        select(Project.id, Project.tenant_id, Project.name, Project.slug, Project.status)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(
            ProjectMembership.user_id == actor.user_id,
            ProjectMembership.status == 'active',
            Project.deleted_at.is_(None),
        )
        .order_by(Project.name)
    ).all()
    global_permissions = effective_permissions(
        db, actor, scope={'scope_type': 'GLOBAL'}
    )
    return {
        'user_id': actor.user_id,
        'organizations': [
            {'id': row.id, 'name': row.name, 'slug': row.slug, 'status': row.status}
            for row in organizations
        ],
        'projects': [
            {
                'id': row.id,
                'organization_id': row.tenant_id,
                'name': row.name,
                'slug': row.slug,
                'status': row.status,
            }
            for row in projects
        ],
        'global_permissions': sorted(global_permissions),
        'assignments': [
            _assignment_public(db, row) for row in _assignments_for_user(db, actor.user_id)
        ],
    }


@router.post('/jit/requests', response_model=JITRequestOutput, status_code=201)
def request_jit(
    data: JITRequestInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    role = _role(db, data.role_id)
    scope = _hydrate_scope(db, _scope_dict(data))
    authorize_or_raise(
        db, actor, 'jit.request', scope=scope,
        context=request_context(request), write=True,
    )
    row = JITAccessRequest(
        requester_id=actor.user_id,
        role_id=role.id,
        scope_type=scope['scope_type'],
        scope_id=scope.get('scope_id'),
        tenant_id=scope.get('tenant_id'),
        project_id=scope.get('project_id'),
        apmid=scope.get('apmid'),
        environment=scope.get('environment'),
        requested_duration_minutes=data.duration_minutes,
        reason=data.reason,
        status='PENDING',
        approval_id=None,
    )
    db.add(row)
    db.flush()
    row.approval_id = f'iam:{row.id}'
    audit(db, request, 'jit.requested', 'iam_jit_requests', row.id)
    return _jit_public(db, row)


@router.get('/jit/requests', response_model=JITRequestPage)
def jit_requests(
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    can_manage = authorize(
        db, actor, 'jit.read', scope={'scope_type': 'GLOBAL'}
    ).allowed
    query = select(JITAccessRequest).order_by(JITAccessRequest.created_at.desc())
    if not can_manage:
        query = query.where(JITAccessRequest.requester_id == actor.user_id)
    rows = db.scalars(query.limit(500)).all()
    return {'items': [_jit_public(db, row) for row in rows], 'total': len(rows)}


@router.post('/jit/requests/{request_id}/approve', response_model=JITRequestOutput)
def approve_jit(
    request_id: str,
    data: JITDecisionInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(JITAccessRequest, request_id)
    if row is None:
        raise HTTPException(404, {'error': 'jit_request_not_found'})
    if row.status != 'PENDING':
        raise HTTPException(409, {'error': 'jit_request_not_pending'})
    role = _role(db, row.role_id)
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
    }))
    authorize_or_raise(
        db, actor, 'jit.approve', scope=scope,
        context=request_context(request), write=True,
    )
    _assert_delegatable(db, actor, role, scope, request)
    _assert_no_role_conflict(db, 'USER', str(row.requester_id), row.role_id, scope)
    assignment = RoleAssignment(
        subject_type='USER',
        subject_id=str(row.requester_id),
        role_id=row.role_id,
        effect='ALLOW',
        scope_type=scope['scope_type'],
        scope_id=scope.get('scope_id'),
        tenant_id=scope.get('tenant_id'),
        project_id=scope.get('project_id'),
        apmid=scope.get('apmid'),
        environment=scope.get('environment'),
        conditions={},
        permission_ceiling=sorted(_expanded_role_permissions(db, role)),
        inherit=True,
        approval_required=False,
        valid_from=now(),
        valid_until=now() + timedelta(minutes=row.requested_duration_minutes),
        enabled=True,
        source='JIT',
        source_ref=row.id,
        created_by=actor.user_id,
    )
    db.add(assignment)
    db.flush()
    row.status = 'APPROVED'
    row.assignment_id = assignment.id
    row.decided_by = actor.user_id
    row.decided_at = now()
    audit(db, request, 'jit.approved', 'iam_jit_requests', row.id)
    audit(db, request, 'role_assignment.created', 'iam_role_assignments', assignment.id)
    return _jit_public(db, row)


@router.post('/jit/requests/{request_id}/reject', response_model=JITRequestOutput)
def reject_jit(
    request_id: str,
    data: JITDecisionInput,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(JITAccessRequest, request_id)
    if row is None:
        raise HTTPException(404, {'error': 'jit_request_not_found'})
    if row.status != 'PENDING':
        raise HTTPException(409, {'error': 'jit_request_not_pending'})
    scope = _hydrate_scope(db, normalize_scope({
        'scope_type': row.scope_type,
        'scope_id': row.scope_id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'apmid': row.apmid,
        'environment': row.environment,
    }))
    authorize_or_raise(
        db, actor, 'jit.reject', scope=scope,
        context=request_context(request), write=True,
    )
    row.status = 'REJECTED'
    row.decided_by = actor.user_id
    row.decided_at = now()
    audit(db, request, 'jit.rejected', 'iam_jit_requests', row.id)
    return _jit_public(db, row)


@router.post('/break-glass', response_model=BreakGlassOutput, status_code=201)
def start_break_glass(
    data: BreakGlassStart,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    if actor.kind != 'session' or actor.user.is_service_account:
        raise HTTPException(403, {'error': 'interactive_human_session_required'})
    authorize_or_raise(
        db, actor, 'break_glass.use', scope={'scope_type': 'GLOBAL'},
        context=request_context(request), write=True,
    )
    active = db.scalar(select(BreakGlassAccess.id).where(
        BreakGlassAccess.user_id == actor.user_id,
        BreakGlassAccess.enabled.is_(True),
        BreakGlassAccess.ended_at.is_(None),
        BreakGlassAccess.valid_until > now(),
    ))
    if active:
        raise HTTPException(409, {'error': 'break_glass_already_active'})
    row = BreakGlassAccess(
        user_id=actor.user_id,
        reason=data.reason,
        valid_from=now(),
        valid_until=now() + timedelta(minutes=data.duration_minutes),
        enabled=True,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    request.state.break_glass_id = row.id
    audit(db, request, 'break_glass.started', 'iam_break_glass', row.id)
    return _break_glass_public(row)


@router.delete('/break-glass/{access_id}', response_model=BreakGlassOutput)
def stop_break_glass(
    access_id: str,
    request: Request,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    row = db.get(BreakGlassAccess, access_id)
    if row is None:
        raise HTTPException(404, {'error': 'break_glass_not_found'})
    if row.user_id != actor.user_id:
        authorize_or_raise(
            db, actor, 'break_glass.manage', scope={'scope_type': 'GLOBAL'},
            context=request_context(request), write=True,
        )
    row.enabled = False
    row.ended_by = actor.user_id
    row.ended_at = now()
    request.state.break_glass_id = row.id
    audit(db, request, 'break_glass.ended', 'iam_break_glass', row.id)
    return _break_glass_public(row)


@router.get('/rbac/access-review', response_model=AccessReviewOutput)
def access_review(
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    authorize_or_raise(db, actor, 'rbac.access_review.read', scope={'scope_type': 'GLOBAL'})
    users = db.scalars(select(User).order_by(User.username).limit(1000)).all()
    items = []
    for user in users:
        generic = _assignments_for_user(db, user.id)
        global_roles = list(db.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        ).scalars())
        tenant_rows = db.execute(
            select(TenantRoleAssignment.tenant_id, Role.name)
            .join(Role, Role.id == TenantRoleAssignment.role_id)
            .where(TenantRoleAssignment.user_id == user.id)
        ).all()
        project_rows = db.execute(
            select(ProjectRoleAssignment.project_id, Project.tenant_id, Role.name)
            .join(Project, Project.id == ProjectRoleAssignment.project_id)
            .join(Role, Role.id == ProjectRoleAssignment.role_id)
            .where(ProjectRoleAssignment.user_id == user.id)
        ).all()
        org_ids = {row.tenant_id for row in generic if row.tenant_id}
        org_ids.update(value for value, _ in tenant_rows)
        org_ids.update(tenant_id for _, tenant_id, _ in project_rows)
        project_ids = {row.project_id for row in generic if row.project_id}
        project_ids.update(project_id for project_id, _, _ in project_rows)
        role_names = set(global_roles)
        role_names.update(_role(db, row.role_id).name for row in generic)
        role_names.update(name for _, name in tenant_rows)
        role_names.update(name for _, _, name in project_rows)
        expirations = sorted(
            [row.valid_until for row in generic if row.valid_until is not None]
        )
        sources = {'GLOBAL'} if global_roles else set()
        sources.update(row.source for row in generic)
        if tenant_rows:
            sources.add('ORGANIZATION')
        if project_rows:
            sources.add('PROJECT')
        # One row per user keeps this endpoint compact; the matrix UI can expand
        # assignments through /rbac/assignments.
        items.append({
            'user_id': user.id,
            'username': user.username,
            'email': user.email,
            'organization_id': next(iter(sorted(org_ids)), None),
            'project_id': next(iter(sorted(project_ids)), None),
            'roles': sorted(role_names),
            'source': sorted(sources),
            'expirations': expirations,
            'last_login_at': user.last_login_at,
        })
    return {'items': items, 'total': len(items)}
