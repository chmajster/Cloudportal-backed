from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from app.models import now
from app.policy_engine.engine import (
    EFFECT_TYPES, OPERATORS, POLICY_TYPES, SCOPE_DIMENSIONS, evaluate,
)
from app.policy_engine.models import PolicyDecision, PolicyDefinition, PolicyException, PolicyVersion
from app.policy_engine.schemas import PolicyInput, PolicyUpdate
from app.tenancy.authorization import Principal, authorize as authorize_tenant


SENSITIVE_KEYS = ("password", "secret", "token", "private_key", "bind_password")


def _scope_level(row) -> str:
    if row.tenant_id is None:
        return "global"
    if row.project_id is None:
        return "tenant"
    return "project"


def _snapshot(row) -> dict:
    return {
        "name": row.name,
        "description": row.description,
        "policy_type": row.policy_type,
        "priority": row.priority,
        "enforcement": row.enforcement,
        "status": row.status,
        "scope_level": _scope_level(row),
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
        "scope": row.scope or {},
        "condition": row.condition or {},
        "effects": row.effects or [],
    }


def policy_public(row) -> dict:
    return {
        "id": row.id,
        **_snapshot(row),
        "version": row.version,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def version_public(row) -> dict:
    return {
        "id": row.id,
        "policy_id": row.policy_id,
        "version": row.version,
        "snapshot": row.snapshot or {},
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }


def exception_public(row) -> dict:
    return {
        "id": row.id,
        "policy_id": row.policy_id,
        "name": row.name,
        "reason": row.reason,
        "ticket": row.ticket,
        "status": row.status,
        "condition": row.condition or {},
        "valid_from": row.valid_from.isoformat() + "Z" if row.valid_from else None,
        "valid_until": row.valid_until.isoformat() + "Z" if row.valid_until else None,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def decision_public(row) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() + "Z" if row.timestamp else None,
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
        "actor_user_id": row.actor_user_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "decision": row.decision,
        "matched_policy_ids": row.matched_policy_ids or [],
        "effects": row.effects or [],
        "trace": row.trace or [],
        "context_summary": row.context_summary or {},
        "dry_run": row.dry_run,
    }


def _selected_scope_tuple(scope):
    return (str(scope.tenant_id), str(scope.project_id))


def _visible_predicate(scope):
    tenant_id, project_id = _selected_scope_tuple(scope)
    return or_(
        PolicyDefinition.tenant_id.is_(None),
        and_(PolicyDefinition.tenant_id == tenant_id, PolicyDefinition.project_id.is_(None)),
        and_(PolicyDefinition.tenant_id == tenant_id, PolicyDefinition.project_id == project_id),
    )


def list_policies(db, scope, *, include_archived=False):
    query = select(PolicyDefinition).where(_visible_predicate(scope))
    if not include_archived:
        query = query.where(PolicyDefinition.status != "archived")
    return db.scalars(query.order_by(
        PolicyDefinition.priority.desc(), PolicyDefinition.name, PolicyDefinition.id
    )).all()


def get_visible_policy(db, policy_id, scope):
    row = db.get(PolicyDefinition, str(policy_id))
    if row is None:
        raise HTTPException(404, "Policy not found")
    tenant_id, project_id = _selected_scope_tuple(scope)
    if row.tenant_id is not None and str(row.tenant_id) != tenant_id:
        raise HTTPException(404, "Policy not found")
    if row.project_id is not None and str(row.project_id) != project_id:
        raise HTTPException(404, "Policy not found")
    return row


def _can_platform_admin(permissions) -> bool:
    return "governance.admin" in set(permissions or ())


def _authorize_target_scope(db, actor, selected_scope, scope_level: str, permissions):
    if scope_level == "global":
        if not _can_platform_admin(permissions):
            raise HTTPException(403, "Platform administrator permission required for global policies")
        return None, None
    if scope_level == "tenant":
        # Tenant-wide policy writes require tenant-wide authority, not merely a
        # project grant inherited from the selected context.
        authorize_tenant(
            db, Principal.from_token(actor), selected_scope.tenant_id,
            "policies.manage", write=True, lock=True,
        )
        return str(selected_scope.tenant_id), None
    return str(selected_scope.tenant_id), str(selected_scope.project_id)


def _authorize_existing_write(db, actor, row, selected_scope, permissions):
    level = _scope_level(row)
    tenant_id, project_id = _authorize_target_scope(db, actor, selected_scope, level, permissions)
    if row.tenant_id != tenant_id or row.project_id != project_id:
        raise HTTPException(403, "Policy is outside the writable scope")


def _save_version(db, row, actor_user_id):
    db.add(PolicyVersion(
        policy_id=row.id,
        version=row.version,
        snapshot=_snapshot(row),
        created_by=actor_user_id,
    ))


def create_policy(db, actor, selected_scope, permissions, data: PolicyInput):
    tenant_id, project_id = _authorize_target_scope(
        db, actor, selected_scope, data.scope_level, permissions
    )
    row = PolicyDefinition(
        name=data.name,
        description=data.description,
        policy_type=data.policy_type,
        priority=data.priority,
        enforcement=data.enforcement,
        status=data.status,
        tenant_id=tenant_id,
        project_id=project_id,
        scope=data.scope,
        condition=data.condition,
        effects=data.effects,
        version=1,
        created_by=actor.user_id,
        updated_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    _save_version(db, row, actor.user_id)
    return row


def update_policy(db, actor, selected_scope, permissions, policy_id: str, data: PolicyUpdate):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    if row.version != data.expected_version:
        raise HTTPException(409, "Policy version conflict")
    tenant_id, project_id = _authorize_target_scope(
        db, actor, selected_scope, data.scope_level, permissions
    )
    row.name = data.name
    row.description = data.description
    row.policy_type = data.policy_type
    row.priority = data.priority
    row.enforcement = data.enforcement
    row.status = data.status
    row.tenant_id = tenant_id
    row.project_id = project_id
    row.scope = data.scope
    row.condition = data.condition
    row.effects = data.effects
    row.version += 1
    row.updated_by = actor.user_id
    db.flush()
    _save_version(db, row, actor.user_id)
    return row


def archive_policy(db, actor, selected_scope, permissions, policy_id: str):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    if row.status != "archived":
        row.status = "archived"
        row.version += 1
        row.updated_by = actor.user_id
        db.flush()
        _save_version(db, row, actor.user_id)
    return row


def versions(db, policy_id: str, selected_scope):
    row = get_visible_policy(db, policy_id, selected_scope)
    return db.scalars(select(PolicyVersion).where(
        PolicyVersion.policy_id == row.id
    ).order_by(PolicyVersion.version.desc())).all()


def rollback_policy(db, actor, selected_scope, permissions, policy_id: str, version: int, expected_version: int):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    if row.version != expected_version:
        raise HTTPException(409, "Policy version conflict")
    source = db.scalar(select(PolicyVersion).where(
        PolicyVersion.policy_id == row.id, PolicyVersion.version == int(version)
    ))
    if source is None:
        raise HTTPException(404, "Policy version not found")
    snap = dict(source.snapshot or {})
    scope_level = str(snap.get("scope_level") or "project")
    tenant_id, project_id = _authorize_target_scope(db, actor, selected_scope, scope_level, permissions)
    for field in ("name", "description", "policy_type", "priority", "enforcement", "status", "scope", "condition", "effects"):
        if field in snap:
            setattr(row, field, snap[field])
    row.tenant_id = tenant_id
    row.project_id = project_id
    row.version += 1
    row.updated_by = actor.user_id
    db.flush()
    _save_version(db, row, actor.user_id)
    return row


def list_exceptions(db, policy_id: str, selected_scope):
    row = get_visible_policy(db, policy_id, selected_scope)
    return db.scalars(select(PolicyException).where(
        PolicyException.policy_id == row.id
    ).order_by(PolicyException.created_at.desc())).all()


def _utc_naive(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def create_exception(db, actor, selected_scope, permissions, policy_id: str, data):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    valid_from = _utc_naive(data.valid_from)
    valid_until = _utc_naive(data.valid_until)
    if valid_from and valid_until and valid_until <= valid_from:
        raise HTTPException(422, "valid_until must be after valid_from")
    exception = PolicyException(
        policy_id=row.id,
        name=data.name,
        reason=data.reason,
        ticket=data.ticket,
        status=data.status,
        condition=data.condition,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by=actor.user_id,
        approved_by=actor.user_id if data.status == "approved" else None,
    )
    db.add(exception)
    db.flush()
    return exception


def approve_exception(db, actor, selected_scope, permissions, policy_id: str, exception_id: str):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    exception = db.get(PolicyException, str(exception_id))
    if exception is None or exception.policy_id != row.id:
        raise HTTPException(404, "Policy exception not found")
    if exception.status == "revoked":
        raise HTTPException(409, "Revoked policy exception cannot be approved")
    exception.status = "approved"
    exception.approved_by = actor.user_id
    db.flush()
    return exception


def revoke_exception(db, actor, selected_scope, permissions, policy_id: str, exception_id: str):
    row = get_visible_policy(db, policy_id, selected_scope)
    _authorize_existing_write(db, actor, row, selected_scope, permissions)
    exception = db.get(PolicyException, str(exception_id))
    if exception is None or exception.policy_id != row.id:
        raise HTTPException(404, "Policy exception not found")
    exception.status = "revoked"
    return exception


def _policies_for_context(db, context: dict):
    tenant_id = str(((context.get("scope") or {}).get("tenant_id") or "")).strip() or None
    project_id = str(((context.get("scope") or {}).get("project_id") or "")).strip() or None
    predicates = [PolicyDefinition.tenant_id.is_(None)]
    if tenant_id:
        predicates.append(and_(PolicyDefinition.tenant_id == tenant_id, PolicyDefinition.project_id.is_(None)))
    if tenant_id and project_id:
        predicates.append(and_(
            PolicyDefinition.tenant_id == tenant_id,
            PolicyDefinition.project_id == project_id,
        ))
    return db.scalars(select(PolicyDefinition).where(
        PolicyDefinition.status.in_(("enforced", "dry_run")),
        or_(*predicates),
    )).all()


def _redact(value: Any, depth=0):
    if depth > 7:
        return "<max-depth>"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                result["..."] = "<truncated>"
                break
            key_text = str(key)[:128]
            if any(secret in key_text.lower() for secret in SENSITIVE_KEYS):
                result[key_text] = "***"
            else:
                result[key_text] = _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        return [_redact(item, depth + 1) for item in values[:50]]
    if isinstance(value, str):
        return value[:1024]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1024]


def _decision_row(db, context: dict, result, *, dry_run=False):
    scope = context.get("scope") or {}
    actor = context.get("actor") or {}
    request = context.get("request") or {}
    resource = context.get("resource") or {}
    row = PolicyDecision(
        tenant_id=str(scope.get("tenant_id")) if scope.get("tenant_id") else None,
        project_id=str(scope.get("project_id")) if scope.get("project_id") else None,
        actor_user_id=int(actor["id"]) if actor.get("id") is not None else None,
        action=str(request.get("action") or "")[:100],
        resource_type=str(resource.get("type") or "resource")[:64],
        resource_id=str(resource.get("id"))[:160] if resource.get("id") is not None else None,
        decision=result.decision,
        matched_policy_ids=result.matched_policy_ids,
        effects=result.applied_effects,
        trace=result.trace,
        context_summary=_redact(context),
        dry_run=bool(dry_run),
    )
    db.add(row)
    db.flush()
    return row


def evaluate_context(db, context: dict, *, persist=False, durable_denies=False):
    policies = _policies_for_context(db, context)
    policy_ids = [row.id for row in policies]
    exceptions = []
    if policy_ids:
        instant = now()
        exceptions = db.scalars(select(PolicyException).where(
            PolicyException.policy_id.in_(policy_ids),
            PolicyException.status == "approved",
            or_(PolicyException.valid_from.is_(None), PolicyException.valid_from <= instant),
            or_(PolicyException.valid_until.is_(None), PolicyException.valid_until > instant),
        )).all()
    result = evaluate(policies, context, exceptions)
    public = result.public()
    if persist:
        if durable_denies and result.decision == "deny":
            # Denied HTTP requests are rolled back by the request transaction.
            # Roll back any staged request mutations first, then persist only
            # the security decision so denied operations remain auditable.
            db.rollback()
            decision = _decision_row(db, context, result)
            db.commit()
        else:
            decision = _decision_row(db, context, result)
        public["decision_id"] = decision.id
    return public


def list_decisions(db, selected_scope, *, limit=100, offset=0, action=None, decision=None):
    tenant_id, project_id = _selected_scope_tuple(selected_scope)
    # Decision logs contain concrete actor/resource context. Unlike policy
    # definitions, they must never inherit global visibility into a project.
    # A project can see its own rows plus tenant-wide rows of the same tenant.
    query = select(PolicyDecision).where(
        PolicyDecision.tenant_id == tenant_id,
        or_(
            PolicyDecision.project_id.is_(None),
            PolicyDecision.project_id == project_id,
        ),
    )
    if action:
        query = query.where(PolicyDecision.action == action)
    if decision:
        query = query.where(PolicyDecision.decision == decision)
    return db.scalars(query.order_by(
        PolicyDecision.timestamp.desc()
    ).offset(offset).limit(limit)).all()


def capabilities():
    return {
        "policy_types": list(POLICY_TYPES),
        "statuses": ["draft", "dry_run", "enforced", "disabled", "archived"],
        "enforcements": ["hard", "soft", "advisory"],
        "operators": sorted(OPERATORS),
        "effects": sorted(EFFECT_TYPES),
        "scope_dimensions": sorted(SCOPE_DIMENSIONS),
        "phases": [
            "pre_request", "pre_approval", "pre_provision", "post_provision",
            "pre_day2", "post_day2", "on_refresh", "on_drift", "on_delete", "scheduled",
        ],
    }
