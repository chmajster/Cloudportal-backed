"""Scoped REST API for production Event Broker subscriptions."""
from __future__ import annotations

import copy
import ipaddress
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, delete, func, or_, select

from app.api.common import Limit, Offset
from app.database import get_db
from app.events.actions import action_types
from app.events.api_schemas import (
    DeadLetterAction,
    EventBrokerSettingsInput,
    EventReplayRequest,
    EventSubscriptionCreate,
    EventSubscriptionPatch,
    EventSubscriptionTest,
)
from app.events.models import (
    EventAttempt,
    EventContext,
    EventDeadLetter,
    EventDelivery,
    EventSubscription,
)
from app.events.security import event_broker_network_policy, redact, resolve_webhook_target
from app.events.service import event_public
from app.events.subscriptions import (
    apply_subscription_data,
    attempt_public,
    broker_dashboard,
    broker_envelope,
    dead_letter_public,
    delivery_public,
    replay_dead_letter,
    replay_event as replay_subscription_event,
    retry_delivery,
    subscription_public,
    test_subscription,
    validate_subscription_config,
)
from app.models import Credential, EventRecord, Setting, Token, User
from app.resource_scope.database import reference_visible
from app.resource_scope.http import require
from app.security.core import audit, encrypt_blob


router = APIRouter(tags=["event-broker"])


def _scope(request: Request):
    value = getattr(request.state, "resource_scope", None)
    if value is None:
        raise HTTPException(500, "Event Broker request has no resource scope")
    return value


def _is_admin(request: Request) -> bool:
    return "event_broker.admin" in getattr(request.state, "permissions", set())


def _subscription_visibility(request: Request):
    scope = _scope(request)
    project = and_(
        EventSubscription.tenant_id == scope.tenant_id,
        EventSubscription.project_id == scope.project_id,
    )
    tenant = and_(
        EventSubscription.tenant_id == scope.tenant_id,
        EventSubscription.project_id.is_(None),
    )
    if _is_admin(request):
        return or_(
            project,
            tenant,
            and_(
                EventSubscription.tenant_id.is_(None),
                EventSubscription.project_id.is_(None),
            ),
        )
    return or_(project, tenant)


def _event_visibility(request: Request):
    scope = _scope(request)
    return and_(
        EventContext.tenant_id == scope.tenant_id,
        EventContext.project_id == scope.project_id,
    )


def _visible_subscription(db, request: Request, subscription_id: str, *, lock=False):
    query = select(EventSubscription).where(
        EventSubscription.id == subscription_id,
        _subscription_visibility(request),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Event subscription not found")
    return row


def _visible_event(db, request: Request, event_id: str):
    query = (
        select(EventRecord)
        .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
        .where(EventRecord.id == event_id)
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Event not found")
    return row


def _visible_delivery(db, request: Request, delivery_id: str, *, lock=False):
    query = (
        select(EventDelivery)
        .join(EventContext, EventContext.event_sequence == EventDelivery.event_sequence)
        .where(EventDelivery.id == delivery_id)
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Event delivery not found")
    return row


def _visible_dead_letter(db, request: Request, dead_id: str, *, lock=False):
    query = (
        select(EventDeadLetter)
        .join(EventDelivery, EventDelivery.id == EventDeadLetter.delivery_id)
        .join(EventContext, EventContext.event_sequence == EventDelivery.event_sequence)
        .where(EventDeadLetter.id == dead_id)
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Dead-letter entry not found")
    return row


def _scope_subscription_data(request: Request, data: dict) -> dict:
    scope_level = str(data.pop("scope_level", "project"))
    scope = _scope(request)
    if scope_level == "project":
        data["tenant_id"] = scope.tenant_id
        data["project_id"] = scope.project_id
    elif scope_level == "tenant":
        if not _is_admin(request):
            raise HTTPException(403, "Tenant-wide Event Broker subscriptions require event_broker.admin")
        data["tenant_id"] = scope.tenant_id
        data["project_id"] = None
    elif scope_level == "global":
        if not _is_admin(request):
            raise HTTPException(403, "Global Event Broker subscriptions require event_broker.admin")
        data["tenant_id"] = None
        data["project_id"] = None
    else:
        raise HTTPException(422, "Unsupported Event Broker scope level")
    return data


def _validate_references(db, request: Request, data: dict, actor):
    scope = _scope(request)
    credential_id = data.get("credential_id")
    if credential_id is not None:
        credential = db.get(Credential, int(credential_id))
        if credential is None:
            raise HTTPException(422, "Selected credential does not exist")
        if data.get("project_id") is not None and not reference_visible(
            db, "credential", credential.id, scope
        ):
            raise HTTPException(403, "Selected credential is not visible in this project")

    token_id = data.get("run_as_token_id")
    if token_id is None:
        data["run_as_token_id"] = actor.id
        return
    token = db.get(Token, int(token_id))
    if token is None or token.user_id != actor.user_id:
        raise HTTPException(403, "run_as_token_id must belong to the subscription owner")
    if token.revoked_at is not None:
        raise HTTPException(409, "Selected run-as token is revoked")


def _base_update(row: EventSubscription) -> dict:
    return {
        "name": row.name,
        "description": row.description,
        "event_pattern": row.event_pattern,
        "phase": row.phase,
        "priority": row.priority,
        "enabled": row.is_enabled,
        "blocking": row.is_blocking,
        "filter": copy.deepcopy(row.filter_json or {}),
        "action_type": row.action_type,
        "action_config": copy.deepcopy(row.action_config or {}),
        "credential_id": row.credential_id,
        "run_as_token_id": row.run_as_token_id,
        "timeout_seconds": row.timeout_seconds,
        "max_attempts": row.max_attempts,
        "retry_delay_seconds": row.retry_delay_seconds,
        "backoff": row.backoff,
        "max_retry_delay_seconds": row.max_retry_delay_seconds,
        "retry_on": list(row.retry_on or []),
        "fail_policy": row.fail_policy,
        "execution_policy": row.execution_policy,
        "stop_on_block": row.stop_on_block,
        "stop_on_failure": row.stop_on_failure,
        "continue_on_failure": row.continue_on_failure,
        "concurrency_limit": row.concurrency_limit,
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "rate_limit_policy": row.rate_limit_policy,
        "dedup_window_seconds": row.dedup_window_seconds,
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
    }


def _cursor(value: str | None):
    if not value:
        return None
    try:
        instant, row_id = value.rsplit("|", 1)
        return datetime.fromisoformat(instant.replace("Z", "+00:00")).replace(tzinfo=None), row_id
    except (TypeError, ValueError):
        raise HTTPException(422, "Invalid cursor") from None


def _next_cursor(rows):
    if not rows:
        return None
    row = rows[-1]
    return row.created_at.isoformat() + "Z|" + str(row.id)


@router.get("/event-broker/overview")
def overview(
    request: Request,
    actor=Depends(require("event_deliveries.read")),
    db=Depends(get_db, scope="function"),
):
    data = broker_dashboard(db)
    if not _is_admin(request):
        scope = _scope(request)
        data["active_subscriptions"] = db.scalar(
            select(func.count())
            .select_from(EventSubscription)
            .where(
                EventSubscription.is_enabled.is_(True),
                _subscription_visibility(request),
            )
        ) or 0
        scoped_deliveries = (
            select(EventDelivery.id)
            .join(EventContext, EventContext.event_sequence == EventDelivery.event_sequence)
            .where(_event_visibility(request))
            .subquery()
        )
        data["pending_deliveries"] = db.scalar(
            select(func.count())
            .select_from(EventDelivery)
            .where(
                EventDelivery.id.in_(select(scoped_deliveries.c.id)),
                EventDelivery.status.in_(["PENDING", "QUEUED", "RETRYING", "RUNNING"]),
            )
        ) or 0
    return data


@router.get("/event-broker/capabilities")
def capabilities(
    actor=Depends(require("event_subscriptions.read")),
):
    return {
        "actions": action_types(),
        "phases": ["PRE", "POST"],
        "decisions": ["CONTINUE", "BLOCK", "MODIFY", "REQUIRE_APPROVAL", "FAIL"],
        "filter_operators": [
            "eq", "neq", "in", "not_in", "contains", "starts_with", "ends_with",
            "exists", "not_exists", "regex", "gt", "gte", "lt", "lte",
        ],
        "filter_logical": ["all", "any", "not"],
        "execution_policies": ["sequential", "parallel", "first_match", "all"],
        "fail_policies": ["open", "closed"],
    }


@router.get("/event-subscriptions")
def subscriptions(
    request: Request,
    enabled: Annotated[bool | None, Query()] = None,
    phase: Annotated[Literal["PRE", "POST"] | None, Query()] = None,
    event_pattern: Annotated[str | None, Query(max_length=128)] = None,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    limit: Limit = 100,
    actor=Depends(require("event_subscriptions.read")),
    db=Depends(get_db, scope="function"),
):
    query = select(EventSubscription).where(_subscription_visibility(request))
    if enabled is not None:
        query = query.where(EventSubscription.is_enabled.is_(enabled))
    if phase:
        query = query.where(EventSubscription.phase == phase)
    if event_pattern:
        query = query.where(EventSubscription.event_pattern == event_pattern)
    decoded = _cursor(cursor)
    if decoded:
        instant, row_id = decoded
        query = query.where(
            or_(
                EventSubscription.created_at < instant,
                and_(EventSubscription.created_at == instant, EventSubscription.id < row_id),
            )
        )
    rows = db.scalars(
        query.order_by(EventSubscription.created_at.desc(), EventSubscription.id.desc()).limit(limit)
    ).all()
    return {"items": [subscription_public(row) for row in rows], "next_cursor": _next_cursor(rows)}


@router.post("/event-subscriptions", status_code=201)
def create_subscription(
    data: EventSubscriptionCreate,
    request: Request,
    actor=Depends(require("event_subscriptions.create")),
    db=Depends(get_db, scope="function"),
):
    raw = _scope_subscription_data(request, data.model_dump(mode="json"))
    _validate_references(db, request, raw, actor)
    row = EventSubscription(id=str(__import__("uuid").uuid4()), created_by=actor.user_id)
    try:
        apply_subscription_data(row, raw, actor.user_id, creating=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    db.add(row)
    db.flush()
    audit(db, request, "event_subscription.created", "event_subscriptions", row.id)
    return subscription_public(row)


@router.get("/event-subscriptions/{subscription_id}")
def subscription(
    subscription_id: str,
    request: Request,
    actor=Depends(require("event_subscriptions.read")),
    db=Depends(get_db, scope="function"),
):
    return subscription_public(_visible_subscription(db, request, subscription_id))


@router.patch("/event-subscriptions/{subscription_id}")
def update_subscription(
    subscription_id: str,
    data: EventSubscriptionPatch,
    request: Request,
    actor=Depends(require("event_subscriptions.update")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_subscription(db, request, subscription_id, lock=True)
    if row.version != data.expected_version:
        raise HTTPException(409, "Event subscription was modified by another request")
    raw = _base_update(row)
    patch = data.model_dump(exclude_unset=True, mode="json")
    patch.pop("expected_version", None)
    clear_secret = bool(patch.pop("clear_secret", False))
    clear_credential = bool(patch.pop("clear_credential", False))
    clear_run_as = bool(patch.pop("clear_run_as_token", False))
    clear_rate = bool(patch.pop("clear_rate_limit", False))
    if clear_credential:
        patch["credential_id"] = None
    if clear_run_as:
        patch["run_as_token_id"] = None
    if clear_rate:
        patch["rate_limit_per_minute"] = None
    raw.update(patch)
    _validate_references(db, request, raw, actor)
    try:
        validate_subscription_config(raw)
        apply_subscription_data(row, raw, actor.user_id, creating=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if clear_secret:
        row.encrypted_action_secret = None
    audit(db, request, "event_subscription.updated", "event_subscriptions", row.id)
    return subscription_public(row)


@router.delete("/event-subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str,
    request: Request,
    actor=Depends(require("event_subscriptions.delete")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_subscription(db, request, subscription_id, lock=True)
    row.is_enabled = False
    db.flush()
    audit(db, request, "event_subscription.deleted", "event_subscriptions", row.id)
    db.delete(row)
    return {"deleted": True, "id": subscription_id}


@router.post("/event-subscriptions/{subscription_id}/enable")
def enable_subscription(
    subscription_id: str,
    request: Request,
    actor=Depends(require("event_subscriptions.enable")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_subscription(db, request, subscription_id, lock=True)
    row.is_enabled = True
    row.version += 1
    row.updated_by = actor.user_id
    audit(db, request, "event_subscription.enabled", "event_subscriptions", row.id)
    return subscription_public(row)


@router.post("/event-subscriptions/{subscription_id}/disable")
def disable_subscription(
    subscription_id: str,
    request: Request,
    actor=Depends(require("event_subscriptions.enable")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_subscription(db, request, subscription_id, lock=True)
    row.is_enabled = False
    row.version += 1
    row.updated_by = actor.user_id
    audit(db, request, "event_subscription.disabled", "event_subscriptions", row.id)
    return subscription_public(row)


@router.post("/event-subscriptions/test")
def test_unsaved_subscription(
    data: EventSubscriptionTest,
    request: Request,
    actor=Depends(require("event_subscriptions.create")),
    db=Depends(get_db, scope="function"),
):
    if data.subscription is None:
        raise HTTPException(422, "subscription is required")
    raw = _scope_subscription_data(request, data.subscription.model_dump(mode="json"))
    _validate_references(db, request, raw, actor)
    if data.event_id:
        event = _visible_event(db, request, data.event_id)
        envelope = broker_envelope(db, event)
    elif data.event:
        envelope = redact(data.event)
        if not isinstance(envelope, dict) or not envelope.get("type"):
            raise HTTPException(422, "Mock event must be an object with type")
    else:
        envelope = {
            "id": "evt_test",
            "type": raw["event_pattern"].replace("*", "sample"),
            "version": "1",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "source": {"service": "cloudportal.event-broker.test"},
            "actor": {"user_id": actor.user_id},
            "scope": {
                "organization_id": raw.get("tenant_id"),
                "project_id": raw.get("project_id"),
            },
            "resource": {"resource_type": "vm", "resource_id": "test-resource"},
            "correlation": {"correlation_id": "event-broker-test"},
            "data": {},
        }
    if data.execute:
        if not _is_admin(request):
            raise HTTPException(403, "Executing a test handler requires event_broker.admin")
        if raw["action_type"] != "webhook":
            raise HTTPException(422, "Live test execution is limited to webhook actions")
        # Resolve now so SSRF/DNS policy is tested without dispatching any infrastructure action.
        config = raw.get("action_config") or {}
        target = str(config.get("url") or "")
        resolve_webhook_target(db, target)
    result = test_subscription(db, raw, envelope, execute=False)
    result["execute_requested"] = bool(data.execute)
    audit(db, request, "event_subscription.tested", "event_subscriptions")
    return result


@router.get("/event-subscription-deliveries")
def deliveries(
    request: Request,
    status: Annotated[str | None, Query(max_length=32)] = None,
    subscription_id: Annotated[str | None, Query(max_length=64)] = None,
    event_id: Annotated[str | None, Query(max_length=64)] = None,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    limit: Limit = 100,
    actor=Depends(require("event_deliveries.read")),
    db=Depends(get_db, scope="function"),
):
    query = select(EventDelivery).join(
        EventContext, EventContext.event_sequence == EventDelivery.event_sequence
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    if status:
        query = query.where(EventDelivery.status == status)
    if subscription_id:
        query = query.where(EventDelivery.subscription_id == subscription_id)
    if event_id:
        query = query.where(EventDelivery.event_id == event_id)
    decoded = _cursor(cursor)
    if decoded:
        instant, row_id = decoded
        query = query.where(
            or_(
                EventDelivery.created_at < instant,
                and_(EventDelivery.created_at == instant, EventDelivery.id < row_id),
            )
        )
    rows = db.scalars(
        query.order_by(EventDelivery.created_at.desc(), EventDelivery.id.desc()).limit(limit)
    ).all()
    return {"items": [delivery_public(row) for row in rows], "next_cursor": _next_cursor(rows)}


@router.get("/event-subscription-deliveries/{delivery_id}")
def delivery(
    delivery_id: str,
    request: Request,
    actor=Depends(require("event_deliveries.read")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_delivery(db, request, delivery_id)
    attempts = db.scalars(
        select(EventAttempt)
        .where(EventAttempt.delivery_id == row.id)
        .order_by(EventAttempt.attempt)
    ).all()
    return {**delivery_public(row), "attempts": [attempt_public(item) for item in attempts]}


@router.post("/event-subscription-deliveries/{delivery_id}/retry", status_code=202)
def retry_subscription_delivery(
    delivery_id: str,
    request: Request,
    actor=Depends(require("event_deliveries.retry")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_delivery(db, request, delivery_id, lock=True)
    try:
        retry_delivery(db, row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, request, "event_delivery.retried", "event_deliveries", row.id)
    return delivery_public(row)


@router.get("/event-dlq")
def dlq(
    request: Request,
    status: Annotated[str | None, Query(max_length=32)] = "OPEN",
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require("event_dlq.read")),
    db=Depends(get_db, scope="function"),
):
    query = (
        select(EventDeadLetter)
        .join(EventDelivery, EventDelivery.id == EventDeadLetter.delivery_id)
        .join(EventContext, EventContext.event_sequence == EventDelivery.event_sequence)
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    if status:
        query = query.where(EventDeadLetter.status == status)
    rows = db.scalars(
        query.order_by(EventDeadLetter.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {"items": [dead_letter_public(row) for row in rows]}


@router.post("/event-dlq/{dead_id}/replay", status_code=202)
def replay_dlq(
    dead_id: str,
    data: DeadLetterAction,
    request: Request,
    actor=Depends(require("event_dlq.replay")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_dead_letter(db, request, dead_id, lock=True)
    try:
        replay, delivery = replay_dead_letter(
            db, row, requested_by=actor.user_id, reason=data.reason
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, request, "event_dlq.replayed", "event_dlq", row.id)
    return {"replay_id": replay.id, "delivery": delivery_public(delivery)}


@router.post("/event-dlq/{dead_id}/ignore")
def ignore_dlq(
    dead_id: str,
    data: DeadLetterAction,
    request: Request,
    actor=Depends(require("event_dlq.delete")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_dead_letter(db, request, dead_id, lock=True)
    row.status = "IGNORED"
    audit(db, request, "event_dlq.ignored", "event_dlq", row.id)
    return dead_letter_public(row)


@router.delete("/event-dlq/{dead_id}")
def delete_dlq(
    dead_id: str,
    request: Request,
    actor=Depends(require("event_dlq.delete")),
    db=Depends(get_db, scope="function"),
):
    row = _visible_dead_letter(db, request, dead_id, lock=True)
    audit(db, request, "event_dlq.deleted", "event_dlq", row.id)
    db.delete(row)
    return {"deleted": True, "id": dead_id}


@router.post("/events/{event_id}/subscription-replay", status_code=202)
def replay_dynamic_subscriptions(
    event_id: str,
    data: EventReplayRequest,
    request: Request,
    actor=Depends(require("events.replay")),
    db=Depends(get_db, scope="function"),
):
    event = _visible_event(db, request, event_id)
    if data.subscription_id:
        _visible_subscription(db, request, data.subscription_id)
    replay, rows = replay_subscription_event(
        db,
        event,
        requested_by=actor.user_id,
        subscription_id=data.subscription_id,
        reason=data.reason,
    )
    audit(db, request, "event.replayed", "events", event.id)
    return {
        "replay_id": replay.id,
        "event_id": event.id,
        "deliveries": [delivery_public(row) for row in rows],
    }


@router.get("/events/{event_id}/timeline")
def event_timeline(
    event_id: str,
    request: Request,
    actor=Depends(require("events.read")),
    db=Depends(get_db, scope="function"),
):
    event = _visible_event(db, request, event_id)
    context = db.get(EventContext, event.sequence)
    envelope = broker_envelope(db, event)
    correlation = envelope.get("correlation") or {}
    root = correlation.get("root_event_id") or event.id
    query = (
        select(EventRecord)
        .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
        .where(or_(EventContext.root_event_id == root, EventRecord.id == root))
        .order_by(EventRecord.created_at, EventRecord.sequence)
        .limit(500)
    )
    if not _is_admin(request):
        query = query.where(_event_visibility(request))
    events = db.scalars(query).all()
    event_ids = [row.id for row in events]
    deliveries = db.scalars(
        select(EventDelivery)
        .where(EventDelivery.event_id.in_(event_ids))
        .order_by(EventDelivery.created_at)
    ).all() if event_ids else []
    return {
        "root_event_id": root,
        "items": [
            {"kind": "event", "timestamp": row.created_at, "event": event_public(row)}
            for row in events
        ] + [
            {"kind": "delivery", "timestamp": row.created_at, "delivery": delivery_public(row)}
            for row in deliveries
        ],
    }


@router.get("/event-broker/settings")
def broker_settings(
    request: Request,
    actor=Depends(require("event_broker.admin")),
    db=Depends(get_db, scope="function"),
):
    return event_broker_network_policy(db)


@router.put("/event-broker/settings")
def update_broker_settings(
    data: EventBrokerSettingsInput,
    request: Request,
    actor=Depends(require("event_broker.admin")),
    db=Depends(get_db, scope="function"),
):
    value = data.model_dump(mode="json")
    for cidr in value["blocked_cidrs"]:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            raise HTTPException(422, f"Invalid blocked CIDR: {cidr}") from None
    normalized_domains = []
    for domain in value["allowed_domains"]:
        domain = str(domain).strip().rstrip(".").lower()
        if not domain or "/" in domain or ":" in domain or "@" in domain:
            raise HTTPException(422, "Invalid allowed domain")
        normalized_domains.append(domain)
    value["allowed_domains"] = sorted(set(normalized_domains))
    row = db.get(Setting, "event_broker")
    if row is None:
        row = Setting(key="event_broker", value=value)
        db.add(row)
    else:
        row.value = value
    audit(db, request, "event_broker.settings_changed", "settings", "event_broker")
    return event_broker_network_policy(db)
