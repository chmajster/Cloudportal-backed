import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.database import session
from app.events.registry import event_matches, extension_by_name, extension_specs
from app.models import (\n    Deployment,\n    EventRecord,\n    Job,\n    ExtensionDelivery,
    ExtensionState,
    WebhookDelivery,
    WebhookEndpoint,
    now,
)


EVENT_TYPE_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{1,127}

def _snapshot_webhook_endpoint_ids(db, event_type: str) -> list[str]:
    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.is_active.is_(True))
    ).all()
    return [
        endpoint.id
        for endpoint in endpoints
        if event_matches(endpoint.events or (), event_type)
    ]


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
    context: dict | None = None,
):
    """Append an event and its dynamic POST deliveries to the caller's transaction."""
    if not EVENT_TYPE_RE.fullmatch(event_type):
        raise ValueError('Invalid event type')

    from app.events.schemas import validate_event_payload
    from app.events.security import redact

    safe_payload = redact(payload or {})
    safe_metadata = redact(metadata or {})
    validate_event_payload(db, event_type, schema_version, safe_payload)

    event_id = str(uuid.uuid4())
    normalized_context = _derive_event_context(
        db,
        event_id=event_id,
        event_type=event_type,
        payload=safe_payload,
        subject_type=subject_type,
        subject_id=str(subject_id),
        request_id=request_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        context=context,
    )
    row = EventRecord(
        id=event_id,
        type=event_type,
        schema_version=schema_version,
        source=source,
        subject_type=subject_type[:64],
        subject_id=str(subject_id)[:255],
        payload=safe_payload,
        metadata_json=safe_metadata,
        routing_json={
            'webhook_endpoint_ids': _snapshot_webhook_endpoint_ids(db, event_type),
        },
        correlation_id=correlation_id,
        causation_id=causation_id,
        request_id=request_id,
        user_id=user_id,
        token_id=token_id,
    )
    db.add(row)
    db.flush()
    _persist_event_context(db, row, normalized_context)

    # Dynamic subscription deliveries are outbox records too: the event,
    # business mutation and fan-out intent commit or roll back together.
    from app.events.subscriptions import materialize_post_deliveries
    materialize_post_deliveries(db, row)
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
                spec.handler(db, event, delivery)
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
        from app.events.subscriptions import dispatch_subscription_deliveries
        delivered += dispatch_subscription_deliveries(db)
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
    result = {
        'latest_sequence': latest,
        'pending_extension_deliveries': pending_extensions,
        'dead_letter_extensions': dead_extensions,
        'pending_webhook_deliveries': pending_webhooks,
        'failed_webhook_deliveries': failed_webhooks,
    }
    from app.events.subscriptions import broker_dashboard
    result.update({'subscriptions': broker_dashboard(db)})
    return result
)


def _derive_event_context(
    db,
    *,
    event_id: str,
    event_type: str,
    payload: dict,
    subject_type: str,
    subject_id: str,
    request_id: str | None,
    correlation_id: str | None,
    causation_id: str | None,
    context: dict | None,
) -> dict:
    """Normalize scope/resource/correlation without coupling producers to broker storage."""
    from app.events.models import EventContext
    from app.events.security import event_broker_network_policy, redact

    value = redact(context or {})
    if not isinstance(value, dict):
        value = {}
    scope = dict(value.get('scope') or {})
    resource = dict(value.get('resource') or {})
    correlation = dict(value.get('correlation') or {})

    job_id = correlation.get('job_id')
    deployment_id = resource.get('deployment_id')
    if isinstance(payload, dict):
        job_data = payload.get('job') if isinstance(payload.get('job'), dict) else {}
        recovery_data = payload.get('recovery') if isinstance(payload.get('recovery'), dict) else {}
        action_data = payload.get('action_request') if isinstance(payload.get('action_request'), dict) else {}
        job_id = job_id or job_data.get('id') or recovery_data.get('job_id')
        deployment_id = (
            deployment_id
            or job_data.get('deployment_id')
            or recovery_data.get('deployment_id')
            or action_data.get('deployment_id')
        )
        resource.setdefault('resource_id', action_data.get('resource_id'))
        if action_data:
            resource.setdefault('resource_type', 'vm')
            resource.setdefault('day2_action', action_data.get('action'))

    if not job_id and subject_type in {'job', 'jobs'} and subject_id:
        job_id = subject_id
    job = db.get(Job, str(job_id)) if job_id else None
    if job is not None:
        correlation.setdefault('job_id', job.id)
        deployment_id = deployment_id or job.deployment_id
        scope.setdefault('organization_id', job.tenant_id)
        scope.setdefault('project_id', job.project_id)

    if not deployment_id and subject_type in {'deployment', 'deployments'} and subject_id:
        deployment_id = subject_id
    deployment = db.get(Deployment, str(deployment_id)) if deployment_id else None
    if deployment is not None:
        resource.setdefault('deployment_id', deployment.id)
        resource.setdefault('provider_id', str(deployment.provider_id))
        resource.setdefault('provider_type', deployment.provider)
        resource.setdefault('resource_type', 'vm')
        resource.setdefault('hostname', deployment.name)
        scope.setdefault('organization_id', deployment.tenant_id)
        scope.setdefault('project_id', deployment.project_id)
        variables = deployment.variables or {}
        scope.setdefault('apmid', variables.get('apmid') or variables.get('APMID'))
        scope.setdefault('environment', variables.get('environment') or variables.get('env'))

    parent_context = None
    if causation_id:
        parent = db.scalar(select(EventRecord).where(EventRecord.id == str(causation_id)).limit(1))
        if parent is not None:
            parent_context = db.get(EventContext, parent.sequence)
            correlation.setdefault('parent_event_id', parent.id)
    depth = int(correlation.get('depth') or 0)
    if parent_context is not None:
        depth = int(parent_context.depth or 0) + 1
        correlation.setdefault('root_event_id', parent_context.root_event_id or parent_context.event_id)
    else:
        correlation.setdefault('root_event_id', event_id)
    correlation['depth'] = depth
    correlation.setdefault('correlation_id', correlation_id)
    correlation.setdefault('request_id', request_id)
    correlation.setdefault('causation_id', causation_id)

    maximum = event_broker_network_policy(db)['max_chain_depth']
    if depth > maximum:
        raise ValueError(f'Event chain depth exceeds configured maximum ({maximum})')

    value['scope'] = {key: item for key, item in scope.items() if item is not None}
    value['resource'] = {key: item for key, item in resource.items() if item is not None}
    value['correlation'] = {key: item for key, item in correlation.items() if item is not None}
    return redact(value)


def _persist_event_context(db, row: EventRecord, context: dict):
    from app.events.models import EventContext

    scope = dict(context.get('scope') or {})
    resource = dict(context.get('resource') or {})
    correlation = dict(context.get('correlation') or {})
    indexed = EventContext(
        event_sequence=row.sequence,
        event_id=row.id,
        tenant_id=scope.get('organization_id') or scope.get('tenant_id'),
        project_id=scope.get('project_id'),
        apmid=scope.get('apmid'),
        environment=scope.get('environment'),
        resource_id=resource.get('resource_id'),
        resource_type=resource.get('resource_type'),
        deployment_id=resource.get('deployment_id'),
        provider_id=str(resource.get('provider_id')) if resource.get('provider_id') is not None else None,
        provider_type=resource.get('provider_type'),
        resource_pool_id=str(resource.get('resource_pool_id')) if resource.get('resource_pool_id') is not None else None,
        blueprint_id=str(resource.get('blueprint_id')) if resource.get('blueprint_id') is not None else None,
        job_id=correlation.get('job_id'),
        workflow_id=str(correlation.get('workflow_id')) if correlation.get('workflow_id') is not None else None,
        parent_event_id=correlation.get('parent_event_id'),
        root_event_id=correlation.get('root_event_id') or row.id,
        depth=int(correlation.get('depth') or 0),
        context_json=context,
    )
    db.add(indexed)
    db.flush()
    return indexed


def _snapshot_webhook_endpoint_ids(db, event_type: str) -> list[str]:
    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.is_active.is_(True))
    ).all()
    return [
        endpoint.id
        for endpoint in endpoints
        if event_matches(endpoint.events or (), event_type)
    ]


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

    from app.events.schemas import validate_event_payload
    validate_event_payload(db, event_type, schema_version, payload or {})

    row = EventRecord(
        id=str(uuid.uuid4()),
        type=event_type,
        schema_version=schema_version,
        source=source,
        subject_type=subject_type[:64],
        subject_id=str(subject_id)[:255],
        payload=payload or {},
        metadata_json=metadata or {},
        routing_json={
            'webhook_endpoint_ids': _snapshot_webhook_endpoint_ids(db, event_type),
        },
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
                spec.handler(db, event, delivery)
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
