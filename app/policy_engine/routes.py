from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api.common import Limit, Offset
from app.database import get_db
from app.policy_engine import service
from app.policy_engine.schemas import (
    EvaluationInput, PolicyConflictInput, PolicyExceptionInput, PolicyImpactInput,
    PolicyInput, PolicyRollback, PolicyStatusChange, PolicyTestInput,
    PolicyUpdate, PreviewInput,
)
from app.projects.authorization import visible_projects
from app.projects.models import Project
from app.resource_scope.http import require
from app.security.core import audit, authenticate
from app.tenancy.authorization import Principal, identity as scoped_identity, visible_tenants
from app.tenancy.models import Tenant
from app.vm_classification import vm_classification_for_tenant

router = APIRouter(tags=["policies"])


def _context_from_input(request: Request, data: EvaluationInput):
    context = dict(data.context or {})
    actor_context = dict(context.get("actor") or {})
    actor_context.setdefault("id", getattr(getattr(request.state, "actor", None), "user_id", None))
    context["actor"] = actor_context

    request_context = dict(context.get("request") or {})
    request_context["action"] = data.action
    request_context.setdefault("phase", "pre_request")
    context["request"] = request_context

    resource = dict(context.get("resource") or {})
    resource["type"] = data.resource_type
    if data.resource_id is not None:
        resource["id"] = data.resource_id
    context["resource"] = resource

    scope = getattr(request.state, "resource_scope", None)
    context_scope = dict(context.get("scope") or {})
    if scope is not None:
        # The authenticated resource scope is authoritative. Never allow the
        # simulator payload to pivot into another tenant/project.
        context_scope["tenant_id"] = str(scope.tenant_id)
        context_scope["project_id"] = str(scope.project_id)
    context["scope"] = context_scope
    return context


@router.get("/policies/capabilities")
def policy_capabilities(actor=Depends(require("policies.read"))):
    return service.capabilities()


@router.get("/policies/scopes")
def policy_scopes(actor=Depends(authenticate), db=Depends(get_db, scope="function")):
    """Return organizations/projects where the actor can manage Policy Engine rules."""
    identity = scoped_identity(db, Principal.from_token(actor))

    tenants = db.scalars(
        select(Tenant)
        .where(
            visible_tenants(identity, permission="policies.manage"),
            Tenant.status == "active",
            Tenant.deleted_at.is_(None),
        )
        .order_by(Tenant.name, Tenant.id)
    ).all()

    projects = db.execute(
        select(Project, Tenant)
        .join(Tenant, Tenant.id == Project.tenant_id)
        .where(
            visible_projects(identity, permission="policies.manage"),
            Project.status == "active",
            Tenant.status == "active",
            Project.deleted_at.is_(None),
            Tenant.deleted_at.is_(None),
        )
        .order_by(Tenant.name, Tenant.id, Project.name, Project.id)
    ).all()

    classification_tenant_ids = {
        str(row.id) for row in tenants
    } | {
        str(tenant.id) for _project, tenant in projects
    }

    return {
        "global_allowed": "governance.admin" in identity.global_permissions,
        "classifications": {
            tenant_id: vm_classification_for_tenant(db, tenant_id)
            for tenant_id in sorted(classification_tenant_ids)
        },
        "tenants": [
            {"id": row.id, "name": row.name, "slug": row.slug}
            for row in tenants
        ],
        "projects": [
            {
                "id": project.id,
                "tenant_id": tenant.id,
                "name": project.name,
                "slug": project.slug,
                "tenant_name": tenant.name,
                "tenant_slug": tenant.slug,
            }
            for project, tenant in projects
        ],
    }


@router.get("/policies/templates")
def policy_templates(actor=Depends(require("policies.read"))):
    return service.templates()


@router.post("/policies/test")
def test_policy_definition(data: PolicyTestInput, request: Request,
                           actor=Depends(require("policies.simulate")),
                           db=Depends(get_db, scope="function")):
    context = _context_from_input(request, data.evaluation)
    return service.test_definition(
        db,
        request.state.resource_scope,
        data.policy,
        context,
        exclude_policy_id=data.exclude_policy_id,
        include_existing=data.include_existing,
    )


@router.post("/policies/conflicts")
def policy_conflicts(data: PolicyConflictInput, request: Request,
                     actor=Depends(require("policies.simulate")),
                     db=Depends(get_db, scope="function")):
    return service.detect_conflicts(
        db,
        request.state.resource_scope,
        data.policy,
        exclude_policy_id=data.exclude_policy_id,
    )


@router.post("/policies/simulate-impact")
def policy_impact(data: PolicyImpactInput, request: Request,
                  actor=Depends(require("policies.simulate")),
                  db=Depends(get_db, scope="function")):
    return service.simulate_impact(
        db,
        request.state.resource_scope,
        data.policy,
        exclude_policy_id=data.exclude_policy_id,
        limit=data.limit,
    )


@router.get("/policies/compliance")
def policy_compliance(request: Request, limit: Limit = 200,
                      actor=Depends(require("policies.read")),
                      db=Depends(get_db, scope="function")):
    return service.compliance(db, request.state.resource_scope, limit=limit)


@router.post("/policies/evaluate")
@router.post("/policies/explain")
def evaluate_policy(data: EvaluationInput, request: Request,
                    actor=Depends(require("policies.simulate")),
                    db=Depends(get_db, scope="function")):
    return service.evaluate_context(db, _context_from_input(request, data), persist=False)


@router.post("/policies/preview")
def preview_policies(data: PreviewInput, request: Request,
                     actor=Depends(require("policies.simulate")),
                     db=Depends(get_db, scope="function")):
    items = [service.evaluate_context(db, _context_from_input(request, item), persist=False)
             for item in data.contexts]
    counts = {"allow": 0, "deny": 0, "approval_required": 0}
    for item in items:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    return {
        "total": len(items),
        "counts": counts,
        "affected": sum(1 for item in items if item["matched_policy_ids"]),
        "items": items,
    }


@router.get("/policies/decisions")
def decisions(request: Request, limit: Limit = 100, offset: Offset = 0,
              action: str | None = Query(default=None, max_length=100),
              decision: Literal["allow", "deny", "approval_required"] | None = None,
              actor=Depends(require("policies.audit")),
              db=Depends(get_db, scope="function")):
    rows = service.list_decisions(
        db, request.state.resource_scope, limit=limit, offset=offset,
        action=action, decision=decision,
    )
    return {"items": [service.decision_public(row) for row in rows]}


@router.get("/policies")
def policies(request: Request, include_archived: bool = False,
             actor=Depends(require("policies.read")),
             db=Depends(get_db, scope="function")):
    return {"items": [
        service.policy_public(row)
        for row in service.list_policies(db, request.state.resource_scope, include_archived=include_archived)
    ]}


@router.post("/policies", status_code=201)
def create_policy(data: PolicyInput, request: Request,
                  actor=Depends(require("policies.manage")),
                  db=Depends(get_db, scope="function")):
    row = service.create_policy(
        db, actor, request.state.resource_scope, request.state.permissions, data
    )
    audit(db, request, "policy.created", "policy_definitions", row.id)
    return service.policy_public(row)


@router.get("/policies/{policy_id}")
def policy(policy_id: str, request: Request,
           actor=Depends(require("policies.read")),
           db=Depends(get_db, scope="function")):
    return service.policy_public(
        service.get_visible_policy(db, policy_id, request.state.resource_scope)
    )


@router.put("/policies/{policy_id}")
def update_policy(policy_id: str, data: PolicyUpdate, request: Request,
                  actor=Depends(require("policies.manage")),
                  db=Depends(get_db, scope="function")):
    row = service.update_policy(
        db, actor, request.state.resource_scope, request.state.permissions, policy_id, data
    )
    audit(db, request, "policy.updated", "policy_definitions", row.id)
    return service.policy_public(row)


@router.post("/policies/{policy_id}/enable")
def enable_policy(policy_id: str, data: PolicyStatusChange, request: Request,
                  actor=Depends(require("policies.manage")),
                  db=Depends(get_db, scope="function")):
    row = service.set_policy_status(
        db, actor, request.state.resource_scope, request.state.permissions,
        policy_id, status="enforced", expected_version=data.expected_version,
    )
    audit(db, request, "policy.enabled", "policy_definitions", row.id)
    return service.policy_public(row)


@router.post("/policies/{policy_id}/disable")
def disable_policy(policy_id: str, data: PolicyStatusChange, request: Request,
                   actor=Depends(require("policies.manage")),
                   db=Depends(get_db, scope="function")):
    row = service.set_policy_status(
        db, actor, request.state.resource_scope, request.state.permissions,
        policy_id, status="disabled", expected_version=data.expected_version,
    )
    audit(db, request, "policy.disabled", "policy_definitions", row.id)
    return service.policy_public(row)


@router.delete("/policies/{policy_id}")
def archive_policy(policy_id: str, request: Request,
                   actor=Depends(require("policies.manage")),
                   db=Depends(get_db, scope="function")):
    row = service.archive_policy(
        db, actor, request.state.resource_scope, request.state.permissions, policy_id
    )
    audit(db, request, "policy.archived", "policy_definitions", row.id)
    return {"archived": True, "policy": service.policy_public(row)}


@router.get("/policies/{policy_id}/versions")
def versions(policy_id: str, request: Request,
             actor=Depends(require("policies.read")),
             db=Depends(get_db, scope="function")):
    return {"items": [
        service.version_public(row)
        for row in service.versions(db, policy_id, request.state.resource_scope)
    ]}


@router.post("/policies/{policy_id}/rollback")
def rollback(policy_id: str, data: PolicyRollback, request: Request,
             actor=Depends(require("policies.manage")),
             db=Depends(get_db, scope="function")):
    row = service.rollback_policy(
        db, actor, request.state.resource_scope, request.state.permissions,
        policy_id, data.version, data.expected_version,
    )
    audit(db, request, "policy.rolled_back", "policy_definitions", row.id)
    return service.policy_public(row)


@router.get("/policies/{policy_id}/exceptions")
def exceptions(policy_id: str, request: Request,
               actor=Depends(require("policies.read")),
               db=Depends(get_db, scope="function")):
    return {"items": [
        service.exception_public(row)
        for row in service.list_exceptions(db, policy_id, request.state.resource_scope)
    ]}


@router.post("/policies/{policy_id}/exceptions", status_code=201)
def create_exception(policy_id: str, data: PolicyExceptionInput, request: Request,
                     actor=Depends(require("policies.exception.manage")),
                     db=Depends(get_db, scope="function")):
    row = service.create_exception(
        db, actor, request.state.resource_scope, request.state.permissions, policy_id, data
    )
    audit(db, request, "policy.exception.created", "policy_exceptions", row.id)
    return service.exception_public(row)


@router.post("/policies/{policy_id}/exceptions/{exception_id}/approve")
def approve_exception(policy_id: str, exception_id: str, request: Request,
                      actor=Depends(require("policies.exception.manage")),
                      db=Depends(get_db, scope="function")):
    row = service.approve_exception(
        db, actor, request.state.resource_scope, request.state.permissions, policy_id, exception_id
    )
    audit(db, request, "policy.exception.approved", "policy_exceptions", row.id)
    return service.exception_public(row)


@router.delete("/policies/{policy_id}/exceptions/{exception_id}")
def revoke_exception(policy_id: str, exception_id: str, request: Request,
                     actor=Depends(require("policies.exception.manage")),
                     db=Depends(get_db, scope="function")):
    row = service.revoke_exception(
        db, actor, request.state.resource_scope, request.state.permissions, policy_id, exception_id
    )
    audit(db, request, "policy.exception.revoked", "policy_exceptions", row.id)
    return service.exception_public(row)
