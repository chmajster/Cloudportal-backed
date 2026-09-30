from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from fastapi import HTTPException, Request
from sqlalchemy import and_, or_, select

from app.models import Permission, Role, RolePermission, Token, User, UserRole, now
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment, TenantRoleGrant
from app.tenancy.authorization import Identity, Principal, identity
from app.policy_engine.engine import evaluate, evaluate_condition, validate_condition_tree
from app.policy_engine.models import PolicyDefinition, PolicyException
from app.rbac.service import ALL_PERMISSIONS
from app.iam.models import (
    BreakGlassAccess, Group, GroupMember, RoleAssignment, RoleProfile, SCOPE_TYPES,
)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    decision: str
    action: str
    permissions: frozenset[str]
    roles: tuple[dict, ...]
    assignments: tuple[dict, ...]
    inherited: tuple[dict, ...]
    denied: tuple[dict, ...]
    reason: str
    trace: tuple[dict, ...]
    break_glass: bool = False
    policy: dict | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == 'ALLOW'

    def public(self) -> dict:
        return {
            'allowed': self.allowed,
            'decision': self.decision,
            'action': self.action,
            'permissions': sorted(self.permissions),
            'roles': list(self.roles),
            'assignments': list(self.assignments),
            'inherited': list(self.inherited),
            'denied': list(self.denied),
            'reason': self.reason,
            'trace': list(self.trace),
            'break_glass': self.break_glass,
            'policy': self.policy,
        }


class PermissionMatcher:
    """Single, conservative wildcard implementation.

    Only a terminal namespace wildcard is accepted (for example machines.*).
    Globs inside segments and arbitrary fnmatch syntax are deliberately rejected.
    """

    @staticmethod
    def validate(value: str) -> str:
        value = str(value or '').strip()
        if value in ALL_PERMISSIONS:
            return value
        if value.endswith('.*') and value.count('*') == 1:
            prefix = value[:-2]
            if prefix and all(part.replace('_', '').replace('-', '').isalnum() for part in prefix.split('.')):
                return value
        raise ValueError(f'Unsupported permission or wildcard: {value}')

    @staticmethod
    def matches(grant: str, action: str) -> bool:
        if grant == action:
            return True
        if grant.endswith('.*') and grant.count('*') == 1:
            prefix = grant[:-2]
            return action.startswith(prefix + '.')
        return False

    @classmethod
    def any_matches(cls, grants: Iterable[str], action: str) -> bool:
        return any(cls.matches(str(grant), action) for grant in grants)


def _instant() -> datetime:
    return now()


def _normalize_environment(value):
    return str(value or '').strip().lower() or None


def normalize_scope(scope: Mapping[str, Any] | None = None, **overrides) -> dict:
    value = dict(scope or {})
    value.update({k: v for k, v in overrides.items() if v is not None})
    scope_type = str(value.get('scope_type') or value.get('type') or 'GLOBAL').upper()
    if scope_type == 'TENANT':
        scope_type = 'ORGANIZATION'
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f'Unsupported scope type: {scope_type}')
    result = {
        'scope_type': scope_type,
        'scope_id': str(value.get('scope_id') or value.get('id') or '') or None,
        'tenant_id': str(value.get('tenant_id') or value.get('organization_id') or '') or None,
        'project_id': str(value.get('project_id') or '') or None,
        'apmid': str(value.get('apmid') or '').strip().upper() or None,
        'environment': _normalize_environment(value.get('environment') or value.get('env')),
        'resource_pool_id': str(value.get('resource_pool_id') or value.get('resource_pool') or '') or None,
        'blueprint_id': str(value.get('blueprint_id') or '') or None,
        'deployment_id': str(value.get('deployment_id') or '') or None,
        'resource_id': str(value.get('resource_id') or '') or None,
        'machine_id': str(value.get('machine_id') or value.get('vm_id') or '') or None,
    }
    if result['scope_type'] == 'ORGANIZATION' and result['scope_id'] and not result['tenant_id']:
        result['tenant_id'] = result['scope_id']
    if result['scope_type'] == 'PROJECT' and result['scope_id'] and not result['project_id']:
        result['project_id'] = result['scope_id']
    return result


def _context(scope: dict, actor, resource: Mapping[str, Any] | None, extra: Mapping[str, Any] | None) -> dict:
    resource_data = dict(resource or {})
    context = {
        'actor': {
            'id': actor.user_id,
            'user_id': actor.user_id,
            'token_id': actor.id,
            'username': getattr(actor.user, 'username', None),
            'service_account': bool(getattr(actor.user, 'is_service_account', False)),
        },
        'scope': {
            'type': scope['scope_type'],
            'id': scope['scope_id'],
            'tenant_id': scope['tenant_id'],
            'organization_id': scope['tenant_id'],
            'project_id': scope['project_id'],
            'apmid': scope['apmid'],
            'environment': scope['environment'],
        },
        'resource': {
            **resource_data,
            'apmid': resource_data.get('apmid', scope['apmid']),
            'environment': _normalize_environment(resource_data.get('environment', scope['environment'])),
            'resource_pool_id': resource_data.get('resource_pool_id', scope['resource_pool_id']),
            'blueprint_id': resource_data.get('blueprint_id', scope['blueprint_id']),
            'deployment_id': resource_data.get('deployment_id', scope['deployment_id']),
            'id': resource_data.get('id') or scope['resource_id'] or scope['machine_id'],
        },
        'request': dict(extra or {}),
    }
    return context


def _subjects(db, actor) -> list[tuple[str, str]]:
    result = [('USER', str(actor.user_id))]
    if getattr(actor, 'id', None) is not None and getattr(actor, 'kind', None) != 'simulation':
        result.append(('API_TOKEN', str(actor.id)))
    if getattr(actor.user, 'is_service_account', False):
        result.append(('SERVICE_ACCOUNT', str(actor.user_id)))
    groups = db.scalars(
        select(Group.id)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == actor.user_id, Group.enabled.is_(True))
    ).all()
    result.extend(('GROUP', str(group_id)) for group_id in groups)
    return result


def _active_assignment_query(subjects, instant):
    subject_clauses = [
        and_(RoleAssignment.subject_type == subject_type, RoleAssignment.subject_id == subject_id)
        for subject_type, subject_id in subjects
    ]
    return select(RoleAssignment).where(
        or_(*subject_clauses),
        RoleAssignment.enabled.is_(True),
        or_(RoleAssignment.valid_from.is_(None), RoleAssignment.valid_from <= instant),
        or_(RoleAssignment.valid_until.is_(None), RoleAssignment.valid_until > instant),
    )


SYSTEM_ADMIN_BASE_PERMISSIONS = frozenset({
    'users.read',
    'roles.read',
    'groups.read',
    'rbac.assignments.read',
    'rbac.assignments.manage',
    'tenants.admin',
    'projects.admin',
    'organizations.read',
})


def is_system_administrator(db, actor) -> bool:
    """Detect platform administrators from live grants without trusting a role label alone.

    Legacy UserRole grants and Enterprise IAM GLOBAL assignments are both accepted.
    Conditional assignments are deliberately excluded because administrator bypass
    must be an unconditional platform-level capability. API-token ceilings still apply.
    """
    try:
        actor_identity = identity(db, Principal.from_token(actor))
    except (AttributeError, HTTPException):
        return False

    if SYSTEM_ADMIN_BASE_PERMISSIONS <= actor_identity.global_permissions:
        return True

    subjects = _subjects(db, actor)
    rows = db.scalars(
        _active_assignment_query(subjects, _instant()).where(
            RoleAssignment.scope_type == 'GLOBAL',
        )
    ).all()
    granted: set[str] = set()
    denied: set[str] = set()
    token_ceiling = tuple(actor.scopes or ()) if getattr(actor, 'kind', None) == 'api' else None

    for row in rows:
        if row.conditions:
            continue
        exact, patterns, _role_name = _role_grants(db, row.role_id)
        expanded = set(exact)
        for permission in ALL_PERMISSIONS:
            if PermissionMatcher.any_matches(patterns, permission):
                expanded.add(permission)
        if row.permission_ceiling is not None:
            expanded = {
                permission for permission in expanded
                if PermissionMatcher.any_matches(row.permission_ceiling, permission)
            }
        if token_ceiling is not None:
            expanded = {
                permission for permission in expanded
                if PermissionMatcher.any_matches(token_ceiling, permission)
            }
        if row.effect == 'DENY':
            denied.update(expanded)
        else:
            granted.update(expanded)

    return SYSTEM_ADMIN_BASE_PERMISSIONS <= (granted - denied)


def _assignment_scope_matches(row: RoleAssignment, target: dict, context: Mapping[str, Any]) -> tuple[bool, bool]:
    kind = row.scope_type
    target_kind = target['scope_type']
    inherited = kind != target_kind

    if kind == 'GLOBAL':
        return (row.inherit or target_kind == 'GLOBAL'), target_kind != 'GLOBAL'

    if kind == 'ORGANIZATION':
        matched = bool(row.tenant_id and row.tenant_id == target['tenant_id'])
        return matched and (row.inherit or target_kind == 'ORGANIZATION'), inherited

    if kind == 'PROJECT':
        matched = bool(row.project_id and row.project_id == target['project_id'])
        return matched and (row.inherit or target_kind == 'PROJECT'), inherited

    resource = context.get('resource', {})
    if kind == 'APMID':
        actual = str(resource.get('apmid') or target.get('apmid') or '').upper()
        matched = bool(row.apmid and row.apmid.upper() == actual)
        if row.project_id:
            matched = matched and row.project_id == target['project_id']
        return matched and (row.inherit or target_kind == 'APMID'), inherited

    if kind == 'ENVIRONMENT':
        actual = _normalize_environment(resource.get('environment') or target.get('environment'))
        matched = bool(row.environment and _normalize_environment(row.environment) == actual)
        if row.project_id:
            matched = matched and row.project_id == target['project_id']
        if row.apmid:
            matched = matched and row.apmid.upper() == str(resource.get('apmid') or target.get('apmid') or '').upper()
        return matched and (row.inherit or target_kind == 'ENVIRONMENT'), inherited

    lookup = {
        'RESOURCE_POOL': resource.get('resource_pool_id') or target.get('resource_pool_id'),
        'BLUEPRINT': resource.get('blueprint_id') or target.get('blueprint_id'),
        'DEPLOYMENT': resource.get('deployment_id') or target.get('deployment_id'),
        'RESOURCE': resource.get('id') or target.get('resource_id'),
        'MACHINE': resource.get('machine_id') or resource.get('id') or target.get('machine_id'),
    }
    actual = lookup.get(kind)
    matched = actual is not None and row.scope_id is not None and str(actual) == str(row.scope_id)
    return matched, False


def _role_grants(db, role_id: int) -> tuple[set[str], set[str], str]:
    role = db.get(Role, role_id)
    if role is None:
        return set(), set(), f'role:{role_id}'
    exact = {p.name for p in role.permissions}
    profile = db.get(RoleProfile, role_id)
    if profile is not None and not profile.enabled:
        return set(), set(), role.name
    patterns = set(profile.permission_patterns or []) if profile is not None else set()
    return exact, patterns, role.name


def _legacy_global(db, actor_identity, action: str):
    # Query live role membership directly. API-token restriction is evaluated
    # independently before this point so terminal wildcards such as machines.*
    # remain useful without weakening the user-permission ceiling.
    roles = db.execute(
        select(Role.id, Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(UserRole.user_id == actor_identity.user_id, Permission.name == action)
    ).all()
    return [{
        'source': 'legacy_global',
        'role_id': role_id,
        'role': name,
        'effect': 'ALLOW',
        'scope_type': 'GLOBAL',
        'scope_id': None,
    } for role_id, name in roles]


def _legacy_scoped(db, actor_identity, action: str, scope: dict):
    result = []
    tenant_id = scope.get('tenant_id')
    project_id = scope.get('project_id')
    if tenant_id:
        rows = db.execute(
            select(TenantRoleAssignment.id, Role.id, Role.name)
            .join(Role, Role.id == TenantRoleAssignment.role_id)
            .join(TenantRoleGrant, TenantRoleGrant.assignment_id == TenantRoleAssignment.id)
            .join(RolePermission, and_(
                RolePermission.role_id == TenantRoleAssignment.role_id,
                RolePermission.permission_id == TenantRoleGrant.permission_id,
            ))
            .join(Permission, Permission.id == TenantRoleGrant.permission_id)
            .join(TenantMembership, and_(
                TenantMembership.tenant_id == TenantRoleAssignment.tenant_id,
                TenantMembership.user_id == TenantRoleAssignment.user_id,
            ))
            .where(
                TenantRoleAssignment.tenant_id == tenant_id,
                TenantRoleAssignment.user_id == actor_identity.user_id,
                TenantMembership.status == 'active',
                Permission.name == action,
            )
        ).all()
        result.extend({
            'source': 'tenant_assignment',
            'assignment_id': assignment_id,
            'role_id': role_id,
            'role': name,
            'effect': 'ALLOW',
            'scope_type': 'ORGANIZATION',
            'scope_id': tenant_id,
            'inherited': scope['scope_type'] != 'ORGANIZATION',
        } for assignment_id, role_id, name in rows)

    if project_id:
        rows = db.execute(
            select(ProjectRoleAssignment.id, Role.id, Role.name)
            .join(Role, Role.id == ProjectRoleAssignment.role_id)
            .join(ProjectRoleGrant, ProjectRoleGrant.assignment_id == ProjectRoleAssignment.id)
            .join(RolePermission, and_(
                RolePermission.role_id == ProjectRoleAssignment.role_id,
                RolePermission.permission_id == ProjectRoleGrant.permission_id,
            ))
            .join(Permission, Permission.id == ProjectRoleGrant.permission_id)
            .join(ProjectMembership, and_(
                ProjectMembership.project_id == ProjectRoleAssignment.project_id,
                ProjectMembership.user_id == ProjectRoleAssignment.user_id,
            ))
            .where(
                ProjectRoleAssignment.project_id == project_id,
                ProjectRoleAssignment.user_id == actor_identity.user_id,
                ProjectMembership.status == 'active',
                Permission.name == action,
            )
        ).all()
        result.extend({
            'source': 'project_assignment',
            'assignment_id': assignment_id,
            'role_id': role_id,
            'role': name,
            'effect': 'ALLOW',
            'scope_type': 'PROJECT',
            'scope_id': project_id,
            'inherited': scope['scope_type'] != 'PROJECT',
        } for assignment_id, role_id, name in rows)
    return result


def _validate_scope_resource(db, scope: dict, *, write: bool):
    project_id = scope.get('project_id')
    tenant_id = scope.get('tenant_id')
    if not project_id and not tenant_id:
        return

    if project_id:
        row = db.execute(
            select(Project, Tenant)
            .join(Tenant, Tenant.id == Project.tenant_id)
            .where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                Tenant.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(404, {'error': 'resource_not_found'})
        project, tenant = row
        if project.status == 'disabled' or tenant.status == 'disabled':
            raise HTTPException(404, {'error': 'resource_not_found'})
        if write and (project.status != 'active' or tenant.status != 'active'):
            raise HTTPException(409, {'error': 'scope_inactive'})
        return

    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None)))
    if tenant is None or tenant.status == 'disabled':
        raise HTTPException(404, {'error': 'resource_not_found'})
    if write and tenant.status != 'active':
        raise HTTPException(409, {'error': 'scope_inactive'})


def _active_break_glass(db, actor):
    if (getattr(actor, 'kind', None) not in {'session', 'simulation'}
            or getattr(actor.user, 'is_service_account', False)):
        return None
    instant = _instant()
    return db.scalar(
        select(BreakGlassAccess).where(
            BreakGlassAccess.user_id == actor.user_id,
            BreakGlassAccess.enabled.is_(True),
            BreakGlassAccess.valid_from <= instant,
            BreakGlassAccess.valid_until > instant,
            BreakGlassAccess.ended_at.is_(None),
        ).order_by(BreakGlassAccess.valid_until.desc())
    )


def _policy_decision(db, context: dict, scope: dict):
    clauses = [
        PolicyDefinition.policy_type == 'access',
        PolicyDefinition.status.in_(('enforced', 'dry_run')),
    ]
    if scope.get('tenant_id'):
        clauses.append(or_(
            PolicyDefinition.tenant_id.is_(None),
            PolicyDefinition.tenant_id == scope['tenant_id'],
        ))
    else:
        clauses.append(PolicyDefinition.tenant_id.is_(None))
    if scope.get('project_id'):
        clauses.append(or_(
            PolicyDefinition.project_id.is_(None),
            PolicyDefinition.project_id == scope['project_id'],
        ))
    else:
        clauses.append(PolicyDefinition.project_id.is_(None))
    policies = db.scalars(select(PolicyDefinition).where(*clauses)).all()
    if not policies:
        return None
    ids = [p.id for p in policies]
    exceptions = db.scalars(
        select(PolicyException).where(
            PolicyException.policy_id.in_(ids),
            PolicyException.status == 'approved',
        )
    ).all()
    return evaluate(policies, context, exceptions)


@dataclass(frozen=True, slots=True)
class SimulatedActor:
    user_id: int
    user: User
    id: int | None = None
    kind: str = 'simulation'
    scopes: tuple[str, ...] = ()


def _simulation_identity(db, user_id: int) -> tuple[SimulatedActor, Identity]:
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.is_locked:
        raise HTTPException(404, {'error': 'user_not_found'})
    permissions = frozenset(db.scalars(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    ))
    return SimulatedActor(user_id=user_id, user=user), Identity(
        user_id=user_id,
        token_id=-1,
        global_permissions=permissions,
        token_ceiling=None,
    )


def authorize(
    db,
    actor,
    action: str,
    *,
    scope: Mapping[str, Any] | None = None,
    resource: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    write: bool = False,
    subject_user_id: int | None = None,
) -> AuthorizationDecision:
    if action not in ALL_PERMISSIONS:
        raise HTTPException(500, {'error': 'unknown_permission', 'permission': action})

    target = normalize_scope(scope)
    _validate_scope_resource(db, target, write=write)
    if subject_user_id is None:
        actor_identity = identity(db, Principal.from_token(actor))
        evaluated_actor = actor
    else:
        evaluated_actor, actor_identity = _simulation_identity(db, subject_user_id)

    token_ceiling = None
    if subject_user_id is None and actor.kind == 'api':
        token_ceiling = tuple(actor.scopes or ())
        if not PermissionMatcher.any_matches(token_ceiling, action):
            return AuthorizationDecision(
                'DENY', action, frozenset(), (), (), (), (),
                'API token scope does not include the required permission',
                ({'stage': 'token_ceiling', 'matched': False},),
            )

    evaluation_context = _context(target, evaluated_actor, resource, context)
    evaluation_context['action'] = action

    break_glass = _active_break_glass(db, evaluated_actor)
    if break_glass is not None:
        return AuthorizationDecision(
            'ALLOW', action, frozenset({action}), (), ({
                'source': 'break_glass',
                'id': break_glass.id,
                'valid_until': break_glass.valid_until.isoformat(),
            },), (), (), 'Active break-glass grant', ({
                'stage': 'break_glass', 'matched': True, 'id': break_glass.id,
            },), True,
        )

    legacy = _legacy_global(db, actor_identity, action)
    legacy += _legacy_scoped(db, actor_identity, action, target)

    subjects = _subjects(db, evaluated_actor)
    assignments = db.scalars(_active_assignment_query(subjects, _instant())).all()
    matched_allow = []
    matched_deny = []
    inherited = []
    role_refs = {}
    trace = []

    for row in assignments:
        scope_match, is_inherited = _assignment_scope_matches(row, target, evaluation_context)
        if not scope_match:
            trace.append({'assignment_id': row.id, 'matched': False, 'reason': 'scope'})
            continue
        profile = db.get(RoleProfile, row.role_id)
        if is_inherited and profile is not None and not profile.inheritance_enabled:
            trace.append({
                'assignment_id': row.id,
                'matched': False,
                'reason': 'role_inheritance_disabled',
            })
            continue
        if row.conditions and not evaluate_condition(row.conditions, evaluation_context):
            trace.append({'assignment_id': row.id, 'matched': False, 'reason': 'condition'})
            continue
        exact, patterns, role_name = _role_grants(db, row.role_id)
        permission_match = action in exact or PermissionMatcher.any_matches(patterns, action)
        if permission_match and row.permission_ceiling is not None:
            permission_match = PermissionMatcher.any_matches(row.permission_ceiling, action)
        if not permission_match:
            trace.append({'assignment_id': row.id, 'matched': False, 'reason': 'permission'})
            continue
        item = {
            'id': row.id,
            'source': row.source,
            'subject_type': row.subject_type,
            'subject_id': row.subject_id,
            'role_id': row.role_id,
            'role': role_name,
            'effect': row.effect,
            'scope_type': row.scope_type,
            'scope_id': row.scope_id,
            'tenant_id': row.tenant_id,
            'project_id': row.project_id,
            'apmid': row.apmid,
            'environment': row.environment,
            'inherited': is_inherited,
            'approval_required': row.approval_required,
            'valid_until': row.valid_until.isoformat() if row.valid_until else None,
        }
        role_refs[row.role_id] = {'id': row.role_id, 'name': role_name}
        if row.effect == 'DENY':
            matched_deny.append(item)
        else:
            matched_allow.append(item)
            if is_inherited:
                inherited.append(item)
        trace.append({'assignment_id': row.id, 'matched': True, 'effect': row.effect})

    for item in legacy:
        role_refs[item['role_id']] = {'id': item['role_id'], 'name': item['role']}
        matched_allow.append(item)
        if item.get('inherited'):
            inherited.append(item)

    if matched_deny:
        return AuthorizationDecision(
            'DENY', action, frozenset(), tuple(role_refs.values()),
            tuple(matched_allow), tuple(inherited), tuple(matched_deny),
            'Explicit DENY matched before ALLOW',
            tuple(trace + [{'stage': 'explicit_deny', 'matched': True}]),
        )

    # API_TOKEN is a restriction boundary, never an independent privilege
    # source. Token scopes and token-scoped DENY/conditions may narrow access,
    # but at least one USER/GROUP/SERVICE_ACCOUNT/legacy grant must exist.
    if subject_user_id is None and getattr(actor, 'kind', None) == 'api':
        base_allow = [
            item for item in matched_allow
            if item.get('subject_type') != 'API_TOKEN'
        ]
        if not base_allow:
            return AuthorizationDecision(
                'DENY', action, frozenset(), tuple(role_refs.values()),
                tuple(matched_allow), tuple(inherited), (),
                'API token cannot elevate beyond its owner permissions',
                tuple(trace + [{'stage': 'token_owner_intersection', 'matched': False}]),
            )

    if not matched_allow:
        return AuthorizationDecision(
            'DENY', action, frozenset(), tuple(role_refs.values()), (), (),
            (), 'Default deny: no applicable ALLOW grant',
            tuple(trace + [{'stage': 'default_deny', 'matched': True}]),
        )

    policy = _policy_decision(db, evaluation_context, target)
    if policy is not None and policy.decision == 'deny':
        return AuthorizationDecision(
            'DENY', action, frozenset(), tuple(role_refs.values()),
            tuple(matched_allow), tuple(inherited), (),
            'Denied by enforced access policy',
            tuple(trace + policy.trace),
            policy=policy.public(),
        )

    approval_assignment = next((item for item in matched_allow if item.get('approval_required')), None)
    if approval_assignment is not None or (policy is not None and policy.decision == 'approval_required'):
        return AuthorizationDecision(
            'REQUIRES_APPROVAL', action, frozenset({action}), tuple(role_refs.values()),
            tuple(matched_allow), tuple(inherited), (),
            'Access is allowed only after approval',
            tuple(trace + (policy.trace if policy is not None else [])),
            policy=policy.public() if policy is not None else None,
        )

    return AuthorizationDecision(
        'ALLOW', action, frozenset({action}), tuple(role_refs.values()),
        tuple(matched_allow), tuple(inherited), (), 'Permission granted',
        tuple(trace + (policy.trace if policy is not None else [])),
        policy=policy.public() if policy is not None else None,
    )


def authorize_or_raise(db, actor, action: str, **kwargs) -> AuthorizationDecision:
    decision = authorize(db, actor, action, **kwargs)
    context = kwargs.get('context') or {}
    request_id = context.get('request_id')
    if decision.decision == 'ALLOW':
        return decision
    if decision.decision == 'REQUIRES_APPROVAL':
        raise HTTPException(409, {
            'error': 'approval_required',
            'required_permission': action,
            'reason': decision.reason,
            'request_id': request_id,
        })
    raise HTTPException(403, {
        'error': 'permission_denied',
        'required_permission': action,
        'reason': decision.reason,
        'request_id': request_id,
    })


def scope_from_resource_scope(scope) -> dict:
    return {
        'scope_type': 'PROJECT',
        'scope_id': scope.project_id,
        'tenant_id': scope.tenant_id,
        'project_id': scope.project_id,
    }


def effective_permissions(db, actor, *, scope=None, resource=None, context=None, subject_user_id=None) -> set[str]:
    result = set()
    for action in sorted(ALL_PERMISSIONS):
        decision = authorize(
            db, actor, action, scope=scope, resource=resource, context=context, write=False,
            subject_user_id=subject_user_id,
        )
        if decision.decision in {'ALLOW', 'REQUIRES_APPROVAL'}:
            result.add(action)
    return result


def validate_assignment_conditions(conditions: Mapping[str, Any] | None):
    validate_condition_tree(conditions or {})


def assignment_status(row: RoleAssignment) -> str:
    instant = _instant()
    if not row.enabled:
        return 'DISABLED'
    if row.valid_from is not None and row.valid_from > instant:
        return 'SCHEDULED'
    if row.valid_until is not None and row.valid_until <= instant:
        return 'EXPIRED'
    return 'ACTIVE'


def permission_patterns_for_role(db, role_id: int) -> list[str]:
    profile = db.get(RoleProfile, role_id)
    return sorted(profile.permission_patterns or []) if profile else []


def validate_permission_patterns(patterns: Iterable[str]) -> list[str]:
    result = []
    for value in patterns:
        normalized = PermissionMatcher.validate(value)
        if normalized not in result:
            result.append(normalized)
    return result


def request_context(request: Request) -> dict:
    return {
        'request_id': getattr(request.state, 'request_id', None),
        'source': getattr(request.state, 'source', 'API'),
        'ip': request.client.host if request.client else '',
        'method': request.method,
        'path': request.url.path,
    }
