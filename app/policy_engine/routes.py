from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.common import Limit, Offset
from app.database import get_db
from app.policy_engine import service
from app.policy_engine.schemas import (
    EvaluationInput, PolicyExceptionInput, PolicyInput, PolicyRollback,
    PolicyUpdate, PreviewInput,
)
from app.resource_scope.http import require
from app.security.core import audit

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
