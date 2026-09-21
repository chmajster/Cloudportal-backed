import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.database import session
from app.events.registry import event_matches, extension_by_name, extension_specs
from app.models import (
    EventRecord,
    ExtensionDelivery,
    ExtensionState,
    WebhookDelivery,
    now,
)


EVENT_TYPE_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{1,127}$')


def publish_event(
    db,
    event_type: str,
    payload: dict,
    *,
    subject_type: str = 'system',
    subject_id: str = '',
    schema_version: int = 1,
    source: str = 'cloudportal.backend',
    request_id: str | None = None,
    user_id: int | None = None,
    token_id: int | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    metadata: dict | None = None,
):
    """Append a durable event to the transactional outbox.

    The caller owns the surrounding DB transaction. If the domain mutation rolls
    back, the event rolls back with it; if it commits, the dispatcher can always
    materialize delivery attempts later.
    """
    if not EVENT_TYPE_RE.fullmatch(event_type):
        raise ValueError('Invalid event type')
    row = EventRecord(
        id=str(uuid.uuid4()),
        type=event_type,
        schema_version=schema_version,
        source=source,
        subject_type=subject_type[:64],
        subject_id=str(subject_id)[:255],
        payload=payload or {},
        metadata_json=metadata or {},
        correlation_id=correlation_id,
        causation_id=causation_id,
        request_id=request_id,
        user_id=user_id,
        token_id=token_id,
    )
    db.add(row)
    db.flush()
    return row


def event_public(row: EventRecord) -> dict:
    return {
        'sequence': row.sequence,
        'id': row.id,
        'type': row.type,
        'schema_version': row.schema_version,
        'source': row.source,
        'subject_type': row.subject_type,
        'subject_id': row.subject_id,
        'payload': row.payload,
        'metadata': row.metadata_json,
        'correlation_id': row.correlation_id,
        'causation_id': row.causation_id,
        'request_id': row.request_id,
        'user_id': row.user_id,
        'token_id': row.token_id,
        'created_at': row.created_at,
    }


def extension_public(state: ExtensionState, spec=None) -> dict:
    spec = spec or extension_by_name(state.name)
    return {
        'name': state.name,
        'version': spec.version if spec else state.version,
        'description': spec.description if spec else '',
        'event_patterns': list(spec.event_patterns) if spec else [],
        'hooks': sorted(spec.hooks) if spec else [],
        'is_enabled': state.is_enabled,
        'status': state.status,
        'config': state.config,
        'last_event_sequence': state.last_event_sequence,
        'failure_count': state.failure_count,
        'last_error': state.last_error,
        'updated_at': state.updated_at,
    }


def sync_extension_states(db):
    states = {row.name: row for row in db.scalars(select(ExtensionState)).all()}
    for spec in extension_specs():
        state = states.get(spec.name)
        if state is None:
            latest_sequence = db.scalar(select(func.max(EventRecord.sequence))) or 0
            state = ExtensionState(
                name=spec.name,
                version=spec.version,
                last_event_sequence=0 if spec.replay_existing_events else latest_sequence,
            )
            db.add(state)
            db.flush()
            states[spec.name] = state
        elif state.version != spec.version:
            state.version = spec.version
    return states


def materialize_extension_deliveries(db, batch_size: int = 500):
    states = sync_extension_states(db)
    created = 0
    for spec in extension_specs():
        state = db.scalar(
            select(ExtensionState)
            .where(ExtensionState.name == spec.name)
            .with_for_update()
        ) or states[spec.name]
        if not state.is_enabled or spec.handler is None:
            continue
        events = db.scalars(
            select(EventRecord)
            .where(EventRecord.sequence > state.last_event_sequence)
            .order_by(EventRecord.sequence)
            .limit(batch_size)
        ).all()
        for event in events:
            if event_matches(spec.event_patterns, event.type):
                exists = db.scalar(
                    select(ExtensionDelivery.id).where(
                        ExtensionDelivery.extension_name == spec.name,
                        ExtensionDelivery.event_sequence == event.sequence,
                        ExtensionDelivery.is_replay.is_(False),
                    ).limit(1)
                )
                if not exists:
                    db.add(ExtensionDelivery(
                        extension_name=spec.name,
                        event_sequence=event.sequence,
                        materialization_key=f'{spec.name}:{event.sequence}',
                        is_replay=False,
                    ))
                    created += 1
            state.last_event_sequence = event.sequence
    db.flush()
    return created


def _retry_delay(attempts: int) -> int:
    return min(3600, 5 * (2 ** max(0, attempts - 1)))


def deliver_extension_deliveries(db, batch_size: int = 100):
    current = now()
    states = sync_extension_states(db)
    deliveries = db.scalars(
        select(ExtensionDelivery)
        .join(
            ExtensionState,
            ExtensionState.name == ExtensionDelivery.extension_name,
        )
        .where(
            ExtensionDelivery.status == 'pending',
            ExtensionDelivery.next_attempt_at <= current,
            ExtensionState.is_enabled.is_(True),
        )
        .order_by(ExtensionDelivery.next_attempt_at, ExtensionDelivery.created_at)
        .with_for_update(skip_locked=True, of=ExtensionDelivery)
        .limit(batch_size)
    ).all()

    handled = 0
    for delivery in deliveries:
        state = states.get(delivery.extension_name)
        spec = extension_by_name(delivery.extension_name)
        if state is None or not state.is_enabled:
            continue
        if spec is None or spec.handler is None:
            delivery.status = 'dead_letter'
            delivery.last_error = 'Extension handler is not installed'
            state.status = 'error'
            state.last_error = delivery.last_error
            continue
        event = db.get(EventRecord, delivery.event_sequence)
        if event is None:
            delivery.status = 'dead_letter'
            delivery.last_error = 'Event no longer exists'
            continue

        try:
            with db.begin_nested():
                spec.handler(db, event)
            delivery.status = 'delivered'
            delivery.delivered_at = now()
            delivery.last_error = None
            state.status = 'healthy'
            state.failure_count = 0
            state.last_error = None
            handled += 1
        except Exception:
            delivery.attempts += 1
            state.failure_count += 1
            state.status = 'degraded'
            state.last_error = 'Extension delivery failed; inspect backend logs'
            if delivery.attempts >= 8:
                delivery.status = 'dead_letter'
                delivery.last_error = 'Extension delivery exhausted retries'
                state.status = 'error'
            else:
                delivery.next_attempt_at = now() + timedelta(seconds=_retry_delay(delivery.attempts))
                delivery.last_error = 'Extension delivery failed; retry scheduled'
    return handled


def dispatch_event_broker_once():
    with session() as db:
        materialize_extension_deliveries(db)
        delivered = deliver_extension_deliveries(db)
        db.commit()
        return delivered


def replay_event(db, event: EventRecord, target: str = 'all'):
    created = []
    for spec in extension_specs():
        if spec.handler is None or not event_matches(spec.event_patterns, event.type):
            continue
        if target == 'webhooks' and spec.name != 'core.webhook-bridge':
            continue
        if target == 'extensions' and spec.name == 'core.webhook-bridge':
            continue
        state = db.get(ExtensionState, spec.name)
        if state is not None and not state.is_enabled:
            continue
        row = ExtensionDelivery(
            extension_name=spec.name,
            event_sequence=event.sequence,
            is_replay=True,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def retry_dead_letter(db, kind: str, delivery_id: str):
    if kind == 'extension':
        row = db.get(ExtensionDelivery, delivery_id)
        allowed = {'dead_letter', 'failed'}
    elif kind == 'webhook':
        row = db.get(WebhookDelivery, delivery_id)
        allowed = {'failed'}
    else:
        raise ValueError('Unknown delivery kind')
    if row is None:
        return None
    if row.status not in allowed:
        raise ValueError('Delivery is not in a retryable terminal state')
    row.status = 'pending'
    row.attempts = 0
    row.next_attempt_at = now()
    row.delivered_at = None
    row.last_error = None
    return row


def broker_stats(db) -> dict:
    latest = db.scalar(select(func.max(EventRecord.sequence))) or 0
    pending_extensions = db.scalar(
        select(func.count()).select_from(ExtensionDelivery).where(ExtensionDelivery.status == 'pending')
    ) or 0
    dead_extensions = db.scalar(
        select(func.count()).select_from(ExtensionDelivery).where(ExtensionDelivery.status == 'dead_letter')
    ) or 0
    pending_webhooks = db.scalar(
        select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == 'pending')
    ) or 0
    failed_webhooks = db.scalar(
        select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == 'failed')
    ) or 0
    return {
        'latest_sequence': latest,
        'pending_extension_deliveries': pending_extensions,
        'dead_letter_extensions': dead_extensions,
        'pending_webhook_deliveries': pending_webhooks,
        'failed_webhook_deliveries': failed_webhooks,
    }
