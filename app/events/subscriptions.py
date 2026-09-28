"""Administrator-managed Event Broker subscription engine."""
from __future__ import annotations

import copy
import json
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, or_, select

from app.events.actions import ActionFailure, action_handler
from app.events.filters import matches_filter, validate_filter
from app.events.models import (
    EventAttempt,
    EventContext,
    EventDeadLetter,
    EventDelivery,
    EventReplay,
    EventSubscription,
)
from app.events.registry import event_matches
from app.events.security import event_broker_network_policy, redact
from app.events.templates import render_template
from app.models import EventRecord, User, now
from app.security.core import encrypt_blob


DELIVERY_TERMINAL = frozenset(
    {"SUCCESS", "FAILED", "TIMED_OUT", "SKIPPED", "DEAD_LETTER", "CANCELLED"}
)
ACTIVE_DELIVERY = frozenset({"PENDING", "QUEUED", "RUNNING", "RETRYING"})
PRE_DECISIONS = frozenset({"CONTINUE", "BLOCK", "MODIFY", "REQUIRE_APPROVAL", "FAIL"})

DEFAULT_RETRY_ON = ["timeout", "http_429", "http_5xx", "connection", "worker_error"]

MODIFY_ALLOWLISTS: dict[str, frozenset[str]] = {
    "resource.vm.provisioning.pre": frozenset(
        {
            "cpu",
            "memory_mb",
            "disk_gb",
            "tags",
            "metadata",
            "networks",
            "disks",
            "placement_constraints",
        }
    ),
    "resource_pool.placement.pre": frozenset(
        {"constraints", "required_capabilities", "preferred_provider_types", "excluded_targets"}
    ),
    "resource.vm.day2.pre": frozenset({"parameters", "reason"}),
}


class BlockingEventFailure(RuntimeError):
    def __init__(self, decision: str, reason: str, *, event_id: str | None = None):
        super().__init__(reason)
        self.decision = decision
        self.reason = reason
        self.event_id = event_id


def subscription_public(row: EventSubscription) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "event_pattern": row.event_pattern,
        "phase": row.phase,
        "priority": row.priority,
        "enabled": row.is_enabled,
        "blocking": row.is_blocking,
        "filter": row.filter_json or {},
        "action_type": row.action_type,
        "action_config": redact(row.action_config or {}),
        "has_secret": bool(row.encrypted_action_secret),
        "credential_id": row.credential_id,
        "run_as_token_id": row.run_as_token_id,
        "timeout_seconds": row.timeout_seconds,
        "max_attempts": row.max_attempts,
        "retry_delay_seconds": row.retry_delay_seconds,
        "backoff": row.backoff,
        "max_retry_delay_seconds": row.max_retry_delay_seconds,
        "retry_on": row.retry_on or [],
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
        "version": row.version,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def subscription_snapshot(row: EventSubscription) -> dict:
    value = subscription_public(row)
    value.pop("has_secret", None)
    value["subscription_id"] = row.id
    return value


def delivery_public(row: EventDelivery) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "event_sequence": row.event_sequence,
        "subscription_id": row.subscription_id,
        "replay_id": row.replay_id,
        "original_delivery_id": row.original_delivery_id,
        "subscription_version": row.subscription_version,
        "subscription_snapshot": redact(row.subscription_snapshot or {}),
        "action_type": row.action_type,
        "status": row.status,
        "blocking": row.blocking,
        "attempt": row.attempt_count,
        "idempotency_key": row.idempotency_key,
        "worker_id": row.worker_id,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        "duration_ms": row.duration_ms,
        "response_code": row.response_code,
        "response_headers": redact(row.response_headers or {}),
        "response_body": _sanitize_response_body(row.response_body),
        "external_job_id": row.external_job_id,
        "decision": row.decision,
        "decision_reason": row.decision_reason,
        "decision_patch": redact(row.decision_patch or {}),
        "error_type": row.error_type,
        "error_message": row.error_message,
        "next_retry_at": row.next_retry_at.isoformat() + "Z" if row.next_retry_at else None,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def attempt_public(row: EventAttempt) -> dict:
    return {
        "id": row.id,
        "delivery_id": row.delivery_id,
        "attempt": row.attempt,
        "status": row.status,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        "duration_ms": row.duration_ms,
        "response_code": row.response_code,
        "response_body": _sanitize_response_body(row.response_body),
        "error_type": row.error_type,
        "error_message": row.error_message,
    }


def dead_letter_public(row: EventDeadLetter) -> dict:
    return {
        "id": row.id,
        "delivery_id": row.delivery_id,
        "event_id": row.event_id,
        "subscription_id": row.subscription_id,
        "status": row.status,
        "attempts": row.attempts,
        "last_error_type": row.last_error_type,
        "last_error_message": row.last_error_message,
        "last_response_code": row.last_response_code,
        "replay_id": row.replay_id,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def replay_public(row: EventReplay) -> dict:
    return {
        "id": row.id,
        "original_event_id": row.original_event_id,
        "original_event_sequence": row.original_event_sequence,
        "target_subscription_id": row.target_subscription_id,
        "requested_by": row.requested_by,
        "reason": row.reason,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }


def _sanitize_response_body(body: str | None) -> str | None:
    if not body:
        return body
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError):
        return str(redact(body))[:65536]
    return json.dumps(redact(decoded), ensure_ascii=False, separators=(",", ":"))[:65536]


def validate_subscription_config(data: dict) -> dict:
    pattern = str(data.get("event_pattern") or "").strip()
    if not pattern or len(pattern) > 128:
        raise ValueError("Event pattern is required and must not exceed 128 characters")
    if any(char.isspace() for char in pattern):
        raise ValueError("Event pattern cannot contain whitespace")

    phase = str(data.get("phase") or "POST").upper()
    if phase not in {"PRE", "POST"}:
        raise ValueError("Subscription phase must be PRE or POST")
    blocking = bool(data.get("blocking", phase == "PRE"))
    if phase == "PRE" and not blocking:
        raise ValueError("PRE subscriptions must be blocking")
    if phase == "POST" and blocking:
        raise ValueError("POST subscriptions cannot be blocking")

    validate_filter(data.get("filter") or {})
    action_type = str(data.get("action_type") or "").strip()
    config = dict(data.get("action_config") or {})
    action_handler(action_type).validate(config, phase=phase)

    timeout = int(data.get("timeout_seconds") or 30)
    attempts = int(data.get("max_attempts") or 3)
    retry_delay = int(data.get("retry_delay_seconds") or 10)
    max_delay = int(data.get("max_retry_delay_seconds") or 300)
    concurrency = int(data.get("concurrency_limit") or 5)
    if not 1 <= timeout <= 120:
        raise ValueError("timeout_seconds must be between 1 and 120")
    if not 1 <= attempts <= 20:
        raise ValueError("max_attempts must be between 1 and 20")
    if not 0 <= retry_delay <= 3600:
        raise ValueError("retry_delay_seconds must be between 0 and 3600")
    if not retry_delay <= max_delay <= 86400:
        raise ValueError("max_retry_delay_seconds must be >= retry delay and <= 86400")
    if not 1 <= concurrency <= 100:
        raise ValueError("concurrency_limit must be between 1 and 100")
    backoff = str(data.get("backoff") or "exponential")
    if backoff not in {"fixed", "exponential"}:
        raise ValueError("backoff must be fixed or exponential")
    fail_policy = str(data.get("fail_policy") or "open")
    if fail_policy not in {"open", "closed"}:
        raise ValueError("fail_policy must be open or closed")
    execution_policy = str(data.get("execution_policy") or "all")
    if execution_policy not in {"sequential", "parallel", "first_match", "all"}:
        raise ValueError("Unsupported execution_policy")
    rate_policy = str(data.get("rate_limit_policy") or "queue")
    if rate_policy not in {"queue", "reject"}:
        raise ValueError("rate_limit_policy must be queue or reject")
    rate = data.get("rate_limit_per_minute")
    if rate is not None and not 1 <= int(rate) <= 100000:
        raise ValueError("rate_limit_per_minute must be between 1 and 100000")

    retry_on = list(data.get("retry_on") or DEFAULT_RETRY_ON)
    allowed_retry = {"timeout", "http_429", "http_5xx", "connection", "worker_error"}
    if not set(retry_on) <= allowed_retry:
        raise ValueError("retry_on contains unsupported conditions")

    result = dict(data)
    result.update(
        {
            "event_pattern": pattern,
            "phase": phase,
            "blocking": blocking,
            "filter": data.get("filter") or {},
            "action_type": action_type,
            "action_config": config,
            "timeout_seconds": timeout,
            "max_attempts": attempts,
            "retry_delay_seconds": retry_delay,
            "max_retry_delay_seconds": max_delay,
            "concurrency_limit": concurrency,
            "backoff": backoff,
            "fail_policy": fail_policy,
            "execution_policy": execution_policy,
            "rate_limit_policy": rate_policy,
            "rate_limit_per_minute": int(rate) if rate is not None else None,
            "retry_on": retry_on,
        }
    )
    return result


def apply_subscription_data(row: EventSubscription, data: dict, actor_id: int, *, creating=False):
    value = validate_subscription_config(data)
    for source, target in (
        ("name", "name"),
        ("description", "description"),
        ("event_pattern", "event_pattern"),
        ("phase", "phase"),
        ("priority", "priority"),
        ("enabled", "is_enabled"),
        ("blocking", "is_blocking"),
        ("filter", "filter_json"),
        ("action_type", "action_type"),
        ("action_config", "action_config"),
        ("credential_id", "credential_id"),
        ("run_as_token_id", "run_as_token_id"),
        ("timeout_seconds", "timeout_seconds"),
        ("max_attempts", "max_attempts"),
        ("retry_delay_seconds", "retry_delay_seconds"),
        ("backoff", "backoff"),
        ("max_retry_delay_seconds", "max_retry_delay_seconds"),
        ("retry_on", "retry_on"),
        ("fail_policy", "fail_policy"),
        ("execution_policy", "execution_policy"),
        ("stop_on_block", "stop_on_block"),
        ("stop_on_failure", "stop_on_failure"),
        ("continue_on_failure", "continue_on_failure"),
        ("concurrency_limit", "concurrency_limit"),
        ("rate_limit_per_minute", "rate_limit_per_minute"),
        ("rate_limit_policy", "rate_limit_policy"),
        ("dedup_window_seconds", "dedup_window_seconds"),
        ("tenant_id", "tenant_id"),
        ("project_id", "project_id"),
    ):
        if source in value:
            setattr(row, target, value[source])
    if creating:
        row.created_by = actor_id
    else:
        row.updated_by = actor_id
        row.version += 1

    if "secret_config" in data:
        secret = data.get("secret_config")
        if secret in (None, {}):
            row.encrypted_action_secret = None
        else:
            if not isinstance(secret, dict):
                raise ValueError("secret_config must be an object")
            row.encrypted_action_secret = encrypt_blob(
                json.dumps(secret, separators=(",", ":")).encode(),
                f"event-subscription:{row.id}",
            )
    return row


def _context(row: EventContext | None) -> dict:
    return copy.deepcopy(row.context_json or {}) if row is not None else {}


def broker_envelope(db, event: EventRecord) -> dict:
    indexed = db.get(EventContext, event.sequence)
    context = _context(indexed)
    actor_data = dict(context.get("actor") or {})
    if event.user_id and not actor_data.get("user_id"):
        user = db.get(User, event.user_id)
        actor_data.update(
            {
                "user_id": event.user_id,
                "username": user.username if user else None,
                "service_account": False,
            }
        )

    scope = dict(context.get("scope") or {})
    resource = dict(context.get("resource") or {})
    correlation = dict(context.get("correlation") or {})
    if indexed is not None:
        scope.setdefault("organization_id", indexed.tenant_id)
        scope.setdefault("project_id", indexed.project_id)
        scope.setdefault("apmid", indexed.apmid)
        scope.setdefault("environment", indexed.environment)
        resource.setdefault("resource_id", indexed.resource_id)
        resource.setdefault("resource_type", indexed.resource_type)
        resource.setdefault("deployment_id", indexed.deployment_id)
        resource.setdefault("provider_id", indexed.provider_id)
        resource.setdefault("provider_type", indexed.provider_type)
        resource.setdefault("resource_pool_id", indexed.resource_pool_id)
        resource.setdefault("blueprint_id", indexed.blueprint_id)
        correlation.setdefault("job_id", indexed.job_id)
        correlation.setdefault("workflow_id", indexed.workflow_id)
        correlation.setdefault("parent_event_id", indexed.parent_event_id)
        correlation.setdefault("root_event_id", indexed.root_event_id or event.id)
        correlation.setdefault("depth", indexed.depth)
    correlation.setdefault("correlation_id", event.correlation_id)
    correlation.setdefault("request_id", event.request_id)
    correlation.setdefault("causation_id", event.causation_id)

    timestamp = event.created_at.isoformat() + "Z"
    return {
        "id": event.id,
        "type": event.type,
        "version": str(event.schema_version),
        "timestamp": timestamp,
        "event": {
            "id": event.id,
            "type": event.type,
            "version": str(event.schema_version),
            "timestamp": timestamp,
        },
        "source": {
            "service": event.source,
            **dict(context.get("source") or {}),
        },
        "actor": redact(actor_data),
        "scope": redact(scope),
        "resource": redact(resource),
        "correlation": redact(correlation),
        "data": redact(event.payload or {}),
    }


def _scope_matches(subscription: EventSubscription, envelope: dict) -> bool:
    scope = envelope.get("scope") or {}
    tenant_id = scope.get("organization_id") or scope.get("tenant_id")
    project_id = scope.get("project_id")
    if subscription.tenant_id is not None and str(subscription.tenant_id) != str(tenant_id):
        return False
    if subscription.project_id is not None and str(subscription.project_id) != str(project_id):
        return False
    return True


def matching_subscriptions(db, event_type: str, envelope: dict, phase: str) -> list[EventSubscription]:
    rows = db.scalars(
        select(EventSubscription)
        .where(
            EventSubscription.is_enabled.is_(True),
            EventSubscription.phase == phase,
        )
        .order_by(EventSubscription.priority.desc(), EventSubscription.created_at, EventSubscription.id)
    ).all()
    return [
        row
        for row in rows
        if event_matches((row.event_pattern,), event_type)
        and _scope_matches(row, envelope)
        and matches_filter(row.filter_json or {}, envelope)
    ]


def _new_delivery(
    db,
    event: EventRecord,
    subscription: EventSubscription,
    *,
    replay: EventReplay | None = None,
    original_delivery_id: str | None = None,
    blocking: bool | None = None,
):
    identity = (
        f"event:{event.id}:subscription:{subscription.id}:v{subscription.version}"
        if replay is None
        else f"replay:{replay.id}:subscription:{subscription.id}:v{subscription.version}"
    )
    existing = db.scalar(
        select(EventDelivery).where(EventDelivery.idempotency_key == identity).limit(1)
    )
    if existing is not None:
        return existing, False
    delivery = EventDelivery(
        event_sequence=event.sequence,
        event_id=event.id,
        subscription_id=subscription.id,
        replay_id=replay.id if replay else None,
        original_delivery_id=original_delivery_id,
        subscription_version=subscription.version,
        subscription_snapshot=subscription_snapshot(subscription),
        encrypted_secret_snapshot=subscription.encrypted_action_secret,
        action_type=subscription.action_type,
        status="QUEUED",
        blocking=subscription.is_blocking if blocking is None else bool(blocking),
        idempotency_key=identity,
        next_retry_at=now(),
    )
    db.add(delivery)
    db.flush()
    return delivery, True


def materialize_post_deliveries(db, event: EventRecord) -> int:
    envelope = broker_envelope(db, event)
    rows = matching_subscriptions(db, event.type, envelope, "POST")
    created = 0
    for subscription in rows:
        _, is_new = _new_delivery(db, event, subscription)
        created += int(is_new)
    return created


def _execution_subscription(delivery: EventDelivery):
    snapshot = dict(delivery.subscription_snapshot or {})
    return SimpleNamespace(
        id=snapshot.get("id") or snapshot.get("subscription_id") or delivery.subscription_id,
        action_config=dict(snapshot.get("action_config") or {}),
        credential_id=snapshot.get("credential_id"),
        timeout_seconds=int(snapshot.get("timeout_seconds") or 30),
        max_attempts=int(snapshot.get("max_attempts") or 3),
        retry_delay_seconds=int(snapshot.get("retry_delay_seconds") or 10),
        max_retry_delay_seconds=int(snapshot.get("max_retry_delay_seconds") or 300),
        backoff=str(snapshot.get("backoff") or "exponential"),
        retry_on=list(snapshot.get("retry_on") or DEFAULT_RETRY_ON),
        is_blocking=bool(snapshot.get("blocking")),
        encrypted_action_secret=delivery.encrypted_secret_snapshot,
        run_as_token_id=snapshot.get("run_as_token_id"),
        created_by=snapshot.get("created_by"),
        tenant_id=snapshot.get("tenant_id"),
        project_id=snapshot.get("project_id"),
    )


def _delay(snapshot: dict, attempts: int) -> int:
    base = int(snapshot.get("retry_delay_seconds") or 10)
    maximum = int(snapshot.get("max_retry_delay_seconds") or 300)
    if str(snapshot.get("backoff") or "exponential") == "fixed":
        return min(maximum, base)
    return min(maximum, base * (2 ** max(0, attempts - 1)))


def _retry_category(failure: ActionFailure) -> str:
    if failure.response_code == 429:
        return "http_429"
    if failure.response_code and 500 <= failure.response_code <= 599:
        return "http_5xx"
    message = str(failure).lower()
    if "timeout" in message:
        return "timeout"
    if "connect" in message or "network" in message or "dns" in message:
        return "connection"
    return "worker_error"


def _dead_letter(db, delivery: EventDelivery):
    delivery.status = "DEAD_LETTER"
    row = db.scalar(
        select(EventDeadLetter).where(EventDeadLetter.delivery_id == delivery.id).limit(1)
    )
    if row is None:
        row = EventDeadLetter(
            delivery_id=delivery.id,
            event_id=delivery.event_id,
            subscription_id=delivery.subscription_id,
        )
        db.add(row)
    row.status = "OPEN"
    row.attempts = delivery.attempt_count
    row.last_error_type = delivery.error_type
    row.last_error_message = delivery.error_message
    row.last_response_code = delivery.response_code


def _finish_attempt(attempt: EventAttempt, *, status: str, started: float):
    attempt.status = status
    attempt.completed_at = now()
    attempt.duration_ms = max(0, int((time.monotonic() - started) * 1000))


def execute_delivery(db, delivery: EventDelivery) -> EventDelivery:
    event = db.get(EventRecord, delivery.event_sequence)
    if event is None:
        delivery.error_type = "EventMissing"
        delivery.error_message = "Original event no longer exists"
        _dead_letter(db, delivery)
        return delivery

    live = db.get(EventSubscription, delivery.subscription_id) if delivery.subscription_id else None
    if live is None or not live.is_enabled:
        delivery.status = "CANCELLED"
        delivery.completed_at = now()
        delivery.error_type = "SubscriptionDisabled"
        delivery.error_message = "Subscription was deleted or disabled before execution"
        return delivery

    snapshot = dict(delivery.subscription_snapshot or {})
    running = db.scalar(
        select(func.count())
        .select_from(EventDelivery)
        .where(
            EventDelivery.subscription_id == delivery.subscription_id,
            EventDelivery.status == "RUNNING",
            EventDelivery.id != delivery.id,
        )
    ) or 0
    if running >= int(snapshot.get("concurrency_limit") or 5):
        delivery.status = "QUEUED"
        delivery.next_retry_at = now() + timedelta(seconds=5)
        return delivery

    rate = snapshot.get("rate_limit_per_minute")
    if rate:
        recent = db.scalar(
            select(func.count())
            .select_from(EventAttempt)
            .join(EventDelivery, EventDelivery.id == EventAttempt.delivery_id)
            .where(
                EventDelivery.subscription_id == delivery.subscription_id,
                EventAttempt.started_at >= now() - timedelta(minutes=1),
            )
        ) or 0
        if recent >= int(rate):
            if snapshot.get("rate_limit_policy") == "reject":
                delivery.error_type = "RateLimitExceeded"
                delivery.error_message = "Subscription rate limit rejected the delivery"
                _dead_letter(db, delivery)
            else:
                delivery.status = "QUEUED"
                delivery.next_retry_at = now() + timedelta(seconds=60)
            return delivery

    delivery.status = "RUNNING"
    delivery.started_at = now()
    delivery.worker_id = "event-dispatcher"
    delivery.attempt_count += 1
    attempt = EventAttempt(
        delivery_id=delivery.id,
        attempt=delivery.attempt_count,
        status="RUNNING",
    )
    db.add(attempt)
    db.flush()
    started = time.monotonic()
    effective = _execution_subscription(delivery)
    envelope = broker_envelope(db, event)
    try:
        result = action_handler(delivery.action_type).execute(
            db, effective, envelope, delivery.id
        )
    except ActionFailure as failure:
        _finish_attempt(attempt, status="FAILED", started=started)
        attempt.response_code = failure.response_code
        attempt.response_body = _sanitize_response_body(failure.response_body)
        attempt.error_type = type(failure).__name__
        attempt.error_message = str(failure)[:1000]
        delivery.response_code = failure.response_code
        delivery.response_body = _sanitize_response_body(failure.response_body)
        delivery.error_type = type(failure).__name__
        delivery.error_message = str(failure)[:1000]
        allowed = set(snapshot.get("retry_on") or DEFAULT_RETRY_ON)
        retryable = failure.retryable and _retry_category(failure) in allowed
        if retryable and delivery.attempt_count < int(snapshot.get("max_attempts") or 3):
            delivery.status = "RETRYING"
            delivery.next_retry_at = now() + timedelta(
                seconds=_delay(snapshot, delivery.attempt_count)
            )
        else:
            _dead_letter(db, delivery)
        return delivery
    except Exception:
        _finish_attempt(attempt, status="FAILED", started=started)
        attempt.error_type = "WorkerError"
        attempt.error_message = "Event action failed; inspect correlation logs"
        delivery.error_type = "WorkerError"
        delivery.error_message = "Event action failed; inspect correlation logs"
        if (
            "worker_error" in set(snapshot.get("retry_on") or DEFAULT_RETRY_ON)
            and delivery.attempt_count < int(snapshot.get("max_attempts") or 3)
        ):
            delivery.status = "RETRYING"
            delivery.next_retry_at = now() + timedelta(
                seconds=_delay(snapshot, delivery.attempt_count)
            )
        else:
            _dead_letter(db, delivery)
        return delivery

    _finish_attempt(attempt, status="SUCCESS", started=started)
    attempt.response_code = result.response_code
    attempt.response_body = _sanitize_response_body(result.response_body)
    delivery.status = "SUCCESS"
    delivery.completed_at = now()
    delivery.duration_ms = attempt.duration_ms
    delivery.response_code = result.response_code
    delivery.response_headers = redact(result.response_headers or {})
    delivery.response_body = _sanitize_response_body(result.response_body)
    delivery.external_job_id = result.external_job_id
    delivery.decision = result.decision
    delivery.decision_reason = result.reason[:1000]
    delivery.decision_patch = redact(result.patch or {})
    delivery.error_type = None
    delivery.error_message = None
    return delivery


def dispatch_subscription_deliveries(db, batch_size: int = 50) -> int:
    due = db.scalars(
        select(EventDelivery)
        .where(
            EventDelivery.status.in_(["PENDING", "QUEUED", "RETRYING"]),
            EventDelivery.blocking.is_(False),
            EventDelivery.next_retry_at <= now(),
        )
        .order_by(EventDelivery.next_retry_at, EventDelivery.created_at, EventDelivery.id)
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    ).all()
    handled = 0
    for delivery in due:
        before = delivery.status
        execute_delivery(db, delivery)
        if delivery.status != before or delivery.status in DELIVERY_TERMINAL:
            handled += 1
    return handled


def _patch_allowed(event_type: str, patch: dict):
    allowed = MODIFY_ALLOWLISTS.get(event_type, frozenset())
    if not allowed:
        raise ValueError("MODIFY is not allowed for this event type")
    unknown = set(patch) - set(allowed)
    if unknown:
        raise ValueError(
            "MODIFY contains protected fields: " + ", ".join(sorted(map(str, unknown)))
        )


def _apply_patch(data: dict, patch: dict) -> dict:
    result = copy.deepcopy(data)
    for key, value in patch.items():
        result[str(key)] = copy.deepcopy(value)
    return result


def _preview_envelope(event_type: str, data: dict, context: dict, actor: dict | None) -> dict:
    correlation = dict((context or {}).get("correlation") or {})
    return {
        "id": "evt_preview",
        "type": event_type,
        "version": "1",
        "timestamp": now().isoformat() + "Z",
        "event": {"id": "evt_preview", "type": event_type, "version": "1"},
        "source": dict((context or {}).get("source") or {"service": "cloudportal.backend"}),
        "actor": redact(actor or (context or {}).get("actor") or {}),
        "scope": redact(dict((context or {}).get("scope") or {})),
        "resource": redact(dict((context or {}).get("resource") or {})),
        "correlation": redact(correlation),
        "data": redact(data),
    }


def evaluate_pre_event(
    db,
    event_type: str,
    data: dict,
    *,
    context: dict | None = None,
    actor: dict | None = None,
):
    preview = _preview_envelope(event_type, data, context or {}, actor)
    subscriptions = matching_subscriptions(db, event_type, preview, "PRE")
    if not subscriptions:
        return {
            "decision": "CONTINUE",
            "reason": "",
            "data": copy.deepcopy(data),
            "event_id": None,
            "deliveries": [],
        }

    from app.events.service import publish_event

    event = publish_event(
        db,
        event_type,
        redact(data),
        subject_type=str((preview.get("resource") or {}).get("resource_type") or "operation"),
        subject_id=str((preview.get("resource") or {}).get("resource_id") or ""),
        source="cloudportal.backend",
        context={**(context or {}), "actor": actor or (context or {}).get("actor") or {}},
    )
    envelope = broker_envelope(db, event)
    effective_data = copy.deepcopy(data)
    delivery_ids = []
    final_decision = "CONTINUE"
    final_reason = ""

    for subscription in subscriptions:
        envelope["data"] = redact(effective_data)
        delivery, _ = _new_delivery(db, event, subscription, blocking=True)
        delivery_ids.append(delivery.id)
        max_attempts = int((delivery.subscription_snapshot or {}).get("max_attempts") or 3)
        while True:
            execute_delivery(db, delivery)
            if delivery.status != "RETRYING" or delivery.attempt_count >= max_attempts:
                break
            delay = max(
                0,
                int((delivery.next_retry_at - now()).total_seconds()),
            )
            if delay:
                time.sleep(min(delay, 30))

        if delivery.status != "SUCCESS":
            policy = str((delivery.subscription_snapshot or {}).get("fail_policy") or "open")
            if policy == "closed":
                final_decision = "FAIL"
                final_reason = "Blocking Event Broker handler failed closed"
                break
            if (delivery.subscription_snapshot or {}).get("stop_on_failure"):
                break
            continue

        decision = str(delivery.decision or "CONTINUE").upper()
        if decision not in PRE_DECISIONS:
            decision = "FAIL"
        if decision == "MODIFY":
            try:
                _patch_allowed(event_type, delivery.decision_patch or {})
                effective_data = _apply_patch(effective_data, delivery.decision_patch or {})
            except ValueError as exc:
                delivery.status = "FAILED"
                delivery.error_type = "InvalidModifyPatch"
                delivery.error_message = str(exc)[:1000]
                if str((delivery.subscription_snapshot or {}).get("fail_policy") or "open") == "closed":
                    final_decision = "FAIL"
                    final_reason = "Blocking handler returned an invalid MODIFY patch"
                    break
            continue
        if decision in {"BLOCK", "FAIL", "REQUIRE_APPROVAL"}:
            final_decision = decision
            final_reason = delivery.decision_reason or decision
            if decision in {"BLOCK", "FAIL"} and not subscription.stop_on_block:
                continue
            break
        if subscription.execution_policy == "first_match":
            break

    return {
        "decision": final_decision,
        "reason": final_reason,
        "data": effective_data,
        "event_id": event.id,
        "deliveries": delivery_ids,
    }


def retry_delivery(db, delivery: EventDelivery):
    if delivery.status not in {"FAILED", "TIMED_OUT", "DEAD_LETTER", "CANCELLED"}:
        raise ValueError("Only terminal failed/cancelled deliveries can be retried")
    delivery.status = "QUEUED"
    delivery.next_retry_at = now()
    delivery.completed_at = None
    delivery.error_type = None
    delivery.error_message = None
    dead = db.scalar(
        select(EventDeadLetter).where(EventDeadLetter.delivery_id == delivery.id).limit(1)
    )
    if dead is not None:
        dead.status = "RETRIED"
    return delivery


def replay_event(
    db,
    event: EventRecord,
    *,
    requested_by: int,
    subscription_id: str | None = None,
    reason: str = "",
):
    envelope = broker_envelope(db, event)
    if subscription_id:
        subscription = db.get(EventSubscription, subscription_id)
        subscriptions = [subscription] if subscription else []
    else:
        subscriptions = matching_subscriptions(db, event.type, envelope, "POST")
    replay = EventReplay(
        original_event_id=event.id,
        original_event_sequence=event.sequence,
        target_subscription_id=subscription_id,
        requested_by=requested_by,
        reason=str(reason or "")[:500],
    )
    db.add(replay)
    db.flush()
    deliveries = []
    for subscription in subscriptions:
        if subscription is None:
            continue
        delivery, _ = _new_delivery(db, event, subscription, replay=replay)
        deliveries.append(delivery)
    return replay, deliveries


def replay_dead_letter(db, dead: EventDeadLetter, *, requested_by: int, reason: str = ""):
    delivery = db.get(EventDelivery, dead.delivery_id)
    if delivery is None:
        raise ValueError("Dead-letter delivery no longer exists")
    event = db.get(EventRecord, delivery.event_sequence)
    if event is None:
        raise ValueError("Original event no longer exists")
    subscription = db.get(EventSubscription, delivery.subscription_id) if delivery.subscription_id else None
    if subscription is None:
        raise ValueError("Original subscription no longer exists")
    replay = EventReplay(
        original_event_id=event.id,
        original_event_sequence=event.sequence,
        target_subscription_id=subscription.id,
        requested_by=requested_by,
        reason=str(reason or "")[:500],
    )
    db.add(replay)
    db.flush()
    replacement, _ = _new_delivery(
        db,
        event,
        subscription,
        replay=replay,
        original_delivery_id=delivery.id,
    )
    dead.status = "REPLAYED"
    dead.replay_id = replay.id
    return replay, replacement


def test_subscription(db, data: dict, envelope: dict, *, execute=False):
    value = validate_subscription_config(data)
    matched = event_matches((value["event_pattern"],), str(envelope.get("type") or ""))
    matched = matched and matches_filter(value.get("filter") or {}, envelope)
    context = {**envelope, "event": envelope}
    rendered = None
    if value["action_type"] == "webhook":
        rendered = render_template(
            value["action_config"].get("payload_template") or envelope,
            context,
        )
    elif value["action_type"] == "awx":
        rendered = render_template(
            value["action_config"].get("extra_vars") or {},
            context,
        )
    elif value["action_type"] == "publish_event":
        rendered = render_template(
            value["action_config"].get("payload_template") or {},
            context,
        )
    return {
        "matched": bool(matched),
        "result": "MATCHED" if matched else "NOT_MATCHED",
        "handler": value["action_type"],
        "rendered_payload": redact(rendered),
        "dry_run": not execute,
    }


def cleanup_event_broker(db, batch_size: int = 1000) -> dict:
    policy = event_broker_network_policy(db)
    current = now()
    removed = {"deliveries": 0, "dlq": 0}
    delivery_cutoff = current - timedelta(days=policy["deliveries_retention_days"])
    dlq_cutoff = current - timedelta(days=policy["dlq_retention_days"])

    dlq_ids = db.scalars(
        select(EventDeadLetter.id)
        .where(EventDeadLetter.created_at < dlq_cutoff)
        .order_by(EventDeadLetter.created_at)
        .limit(batch_size)
    ).all()
    if dlq_ids:
        removed["dlq"] = db.execute(
            delete(EventDeadLetter).where(EventDeadLetter.id.in_(dlq_ids))
        ).rowcount or 0

    delivery_ids = db.scalars(
        select(EventDelivery.id)
        .where(
            EventDelivery.created_at < delivery_cutoff,
            EventDelivery.status.in_(list(DELIVERY_TERMINAL)),
        )
        .order_by(EventDelivery.created_at)
        .limit(batch_size)
    ).all()
    if delivery_ids:
        removed["deliveries"] = db.execute(
            delete(EventDelivery).where(EventDelivery.id.in_(delivery_ids))
        ).rowcount or 0
    return removed


def broker_dashboard(db) -> dict:
    since_hour = now() - timedelta(hours=1)
    since_minute = now() - timedelta(minutes=1)
    total_hour = db.scalar(
        select(func.count()).select_from(EventRecord).where(EventRecord.created_at >= since_hour)
    ) or 0
    total_minute = db.scalar(
        select(func.count()).select_from(EventRecord).where(EventRecord.created_at >= since_minute)
    ) or 0
    success = db.scalar(
        select(func.count()).select_from(EventDelivery).where(EventDelivery.status == "SUCCESS")
    ) or 0
    terminal = db.scalar(
        select(func.count()).select_from(EventDelivery).where(
            EventDelivery.status.in_(["SUCCESS", "FAILED", "TIMED_OUT", "DEAD_LETTER", "CANCELLED"])
        )
    ) or 0
    durations = db.scalars(
        select(EventDelivery.duration_ms)
        .where(EventDelivery.duration_ms.is_not(None))
        .order_by(EventDelivery.duration_ms.desc())
        .limit(1000)
    ).all()
    ordered = sorted(int(value) for value in durations if value is not None)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0
    return {
        "events_per_minute": int(total_minute),
        "events_per_hour": int(total_hour),
        "success_rate": round((success / terminal) * 100, 2) if terminal else 100.0,
        "failed_deliveries": db.scalar(
            select(func.count()).select_from(EventDelivery).where(
                EventDelivery.status.in_(["FAILED", "DEAD_LETTER", "TIMED_OUT"])
            )
        ) or 0,
        "retries": db.scalar(
            select(func.count()).select_from(EventDelivery).where(EventDelivery.attempt_count > 1)
        ) or 0,
        "dlq": db.scalar(
            select(func.count()).select_from(EventDeadLetter).where(EventDeadLetter.status == "OPEN")
        ) or 0,
        "active_subscriptions": db.scalar(
            select(func.count()).select_from(EventSubscription).where(EventSubscription.is_enabled.is_(True))
        ) or 0,
        "average_handler_duration_ms": round(
            sum(ordered) / len(ordered), 2
        ) if ordered else 0,
        "p95_handler_duration_ms": p95,
        "blocking_failures": db.scalar(
            select(func.count()).select_from(EventDelivery).where(
                EventDelivery.blocking.is_(True),
                EventDelivery.status.in_(["FAILED", "DEAD_LETTER", "TIMED_OUT"]),
            )
        ) or 0,
        "timeouts": db.scalar(
            select(func.count()).select_from(EventDelivery).where(EventDelivery.status == "TIMED_OUT")
        ) or 0,
        "pending_deliveries": db.scalar(
            select(func.count()).select_from(EventDelivery).where(
                EventDelivery.status.in_(["PENDING", "QUEUED", "RETRYING", "RUNNING"])
            )
        ) or 0,
    }
