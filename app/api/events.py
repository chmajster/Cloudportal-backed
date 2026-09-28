import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.api.common import Limit, Offset, idempotent
from app.database import get_db
from app.events.registry import extension_by_name, extension_specs, hook_names
from app.events.schemas import EventSchemaValidationError
from app.events.service import (
    broker_stats,
    event_public,
    extension_public,
    publish_event,
    replay_event,
    retry_dead_letter,
    sync_extension_states,
)
from app.events.models import EventContext, EventSubscription
from app.models import EventRecord, ExtensionDelivery, ExtensionState, WebhookDelivery
from app.resource_scope.http import require as scoped_require
from app.security.core import audit, require as global_require


router = APIRouter(tags=['events'])

SENSITIVE_EVENT_KEYS = {
    'password', 'secret', 'token', 'access_token', 'refresh_token', 'api_key',
    'private_key', 'token_secret', 'authorization', 'credential_secret',
}


def _reject_sensitive_keys(value, path='payload'):
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace('-', '_')
            if normalized in SENSITIVE_EVENT_KEYS or normalized.endswith('_password') or normalized.endswith('_secret'):
                raise ValueError(f'Sensitive field is not allowed in event data: {path}.{key}')
            _reject_sensitive_keys(nested, f'{path}.{key}')
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive_keys(nested, f'{path}[{index}]')
    return value


class EventPublishInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Annotated[
        str,
        Field(
            min_length=8,
            max_length=128,
            pattern=r'^custom\.[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$',
        ),
    ]
    payload: dict = Field(default_factory=dict)
    subject_type: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9_.-]{0,63}$'),
    ] = 'custom'
    subject_id: Annotated[str, Field(max_length=255)] = ''
    schema_version: int = Field(default=1, ge=1, le=1000)
    correlation_id: Annotated[str | None, Field(max_length=64)] = None
    causation_id: Annotated[str | None, Field(max_length=64)] = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def no_secrets(self):
        _reject_sensitive_keys(self.payload, 'payload')
        _reject_sensitive_keys(self.metadata, 'metadata')
        return self


class EventReplayInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Literal['all', 'webhooks', 'extensions'] = 'all'
    include_subscriptions: bool = True
    subscription_id: Annotated[str | None, Field(max_length=64)] = None
    reason: Annotated[str, Field(max_length=500)] = ''


class ExtensionUpdateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_enabled: bool
    config: dict = Field(default_factory=dict)

    @field_validator('config')
    @classmethod
    def non_secret_config(cls, value):
        return _reject_sensitive_keys(value, 'config')


def _event_scope_filter(request: Request):
    scope = getattr(request.state, 'resource_scope', None)
    if scope is None:
        raise HTTPException(500, 'Event request has no resource scope')
    return (
        EventContext.tenant_id == scope.tenant_id,
        EventContext.project_id == scope.project_id,
    )


def _event_admin(request: Request) -> bool:
    return 'event_broker.admin' in getattr(request.state, 'permissions', set())


def _event_query(request: Request, *, all_scopes: bool = False):
    query = select(EventRecord).join(
        EventContext, EventContext.event_sequence == EventRecord.sequence
    )
    if all_scopes:
        if not _event_admin(request):
            raise HTTPException(403, 'event_broker.admin is required for all-scopes event access')
        return query
    return query.where(*_event_scope_filter(request))


def _visible_event(db, request: Request, event_id: str):
    row = db.scalar(_event_query(request).where(EventRecord.id == event_id))
    if row is None:
        raise HTTPException(404, 'Event not found')
    return row


def delivery_public(row, kind):
    return {
        'kind': kind,
        'id': row.id,
        'event_sequence': getattr(row, 'event_sequence', None),
        'event_id': getattr(row, 'event_id', None),
        'consumer': getattr(row, 'extension_name', None) or getattr(row, 'endpoint_id', None),
        'event': getattr(row, 'event', None),
        'status': row.status,
        'attempts': row.attempts,
        'next_attempt_at': row.next_attempt_at,
        'delivered_at': row.delivered_at,
        'last_error': row.last_error,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
        'is_replay': getattr(row, 'is_replay', False),
    }


@router.get('/events')
def events(
    request: Request,
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    subject_type: Annotated[str | None, Query(max_length=64)] = None,
    subject_id: Annotated[str | None, Query(max_length=255)] = None,
    correlation_id: Annotated[str | None, Query(max_length=64)] = None,
    organization_id: Annotated[str | None, Query(max_length=64)] = None,
    project_id: Annotated[str | None, Query(max_length=64)] = None,
    apmid: Annotated[str | None, Query(max_length=64)] = None,
    environment: Annotated[str | None, Query(max_length=32)] = None,
    resource_id: Annotated[str | None, Query(max_length=255)] = None,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    all_scopes: bool = False,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(scoped_require('events.read')),
    db=Depends(get_db, scope='function'),
):
    query = _event_query(request, all_scopes=all_scopes)
    if event_type:
        query = query.where(EventRecord.type == event_type)
    if subject_type:
        query = query.where(EventRecord.subject_type == subject_type)
    if subject_id:
        query = query.where(EventRecord.subject_id == subject_id)
    if correlation_id:
        query = query.where(EventRecord.correlation_id == correlation_id)
    if organization_id:
        if not _event_admin(request) and organization_id != getattr(request.state.resource_scope, 'tenant_id', None):
            raise HTTPException(403, 'Requested organization is outside the selected scope')
        query = query.where(EventContext.tenant_id == organization_id)
    if project_id:
        if not _event_admin(request) and project_id != getattr(request.state.resource_scope, 'project_id', None):
            raise HTTPException(403, 'Requested project is outside the selected scope')
        query = query.where(EventContext.project_id == project_id)
    if apmid:
        query = query.where(EventContext.apmid == apmid)
    if environment:
        query = query.where(EventContext.environment == environment)
    if resource_id:
        query = query.where(EventContext.resource_id == resource_id)
    if after_sequence is not None:
        if offset:
            raise HTTPException(422, 'offset cannot be combined with after_sequence')
        query = query.where(EventRecord.sequence > after_sequence)
        order = EventRecord.sequence.asc()
    else:
        order = EventRecord.sequence.desc()
    rows = db.scalars(query.order_by(order).offset(offset).limit(limit)).all()
    items = []
    for row in rows:
        value = event_public(row)
        context = db.get(EventContext, row.sequence)
        value['context'] = context.context_json if context else {}
        items.append(value)
    return {
        'items': items,
        'next_after_sequence': max((row.sequence for row in rows), default=after_sequence),
    }


@router.get('/events/stats')
def event_stats(request: Request, actor=Depends(scoped_require('events.read')), db=Depends(get_db, scope='function')):
    # Existing aggregate counters remain global only for Event Broker admins.
    # Project users get scoped counters from the production broker overview route.
    if not _event_admin(request):
        scope_filters = _event_scope_filter(request)
        count = db.scalar(
            select(func.count()).select_from(EventRecord)
            .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
            .where(*scope_filters)
        ) or 0
        return {'events_in_scope': count}
    return broker_stats(db)


@router.get('/events/types')
def event_types(actor=Depends(scoped_require('events.read')), db=Depends(get_db, scope='function')):
    persisted = db.scalars(select(EventRecord.type).distinct().order_by(EventRecord.type)).all()
    return {
        'persisted': list(persisted),
        'extension_patterns': sorted({pattern for spec in extension_specs() for pattern in spec.event_patterns}),
        'hooks': list(hook_names()),
    }


@router.get('/events/{event_id}')
def event(
    event_id: str,
    request: Request,
    actor=Depends(scoped_require('events.read')),
    db=Depends(get_db, scope='function'),
):
    row = _visible_event(db, request, event_id)
    value = event_public(row)
    context = db.get(EventContext, row.sequence)
    value['context'] = context.context_json if context else {}
    return value


@router.post('/events', status_code=201)
def create_event(
    data: EventPublishInput,
    request: Request,
    actor=Depends(scoped_require('events.publish')),
    db=Depends(get_db, scope='function'),
):
    encoded_event_data = json.dumps(
        {'payload': data.payload, 'metadata': data.metadata},
        separators=(',', ':'),
        default=str,
    ).encode()
    if len(encoded_event_data) > 256 * 1024:
        raise HTTPException(413, 'Event payload and metadata exceed 256 KiB')

    def create():
        try:
            row = publish_event(
                db,
                data.type,
                data.payload,
                subject_type=data.subject_type,
                subject_id=data.subject_id,
                schema_version=data.schema_version,
                source='cloudportal.api',
                request_id=request.state.request_id,
                user_id=actor.user_id,
                token_id=actor.id,
                correlation_id=data.correlation_id,
                causation_id=data.causation_id,
                metadata=data.metadata,
                context={
                    'scope': {
                        'organization_id': request.state.resource_scope.tenant_id,
                        'project_id': request.state.resource_scope.project_id,
                    },
                    'actor': {
                        'user_id': actor.user_id,
                        'service_account': bool(getattr(actor.user, 'is_service_account', False)),
                    },
                },
            )
            audit(db, request, 'event.published', 'events', row.id)
        except EventSchemaValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        return event_public(row)

    return idempotent(db, request, actor, data.model_dump(mode='json'), create, required=True)


@router.post('/events/{event_id}/replay', status_code=202)
def replay(
    event_id: str,
    data: EventReplayInput,
    request: Request,
    actor=Depends(scoped_require('events.replay')),
    db=Depends(get_db, scope='function'),
):
    row = _visible_event(db, request, event_id)
    legacy = replay_event(db, row, data.target)
    dynamic = []
    replay_id = None
    if data.include_subscriptions:
        from app.events.subscriptions import replay_event as replay_dynamic
        if data.subscription_id:
            subscription = db.get(EventSubscription, data.subscription_id)
            if subscription is None:
                raise HTTPException(404, 'Event subscription not found')
            scope = request.state.resource_scope
            if not _event_admin(request) and not (
                str(subscription.tenant_id) == str(scope.tenant_id)
                and subscription.project_id in {None, scope.project_id}
            ):
                raise HTTPException(404, 'Event subscription not found')
        replay_row, dynamic = replay_dynamic(
            db,
            row,
            requested_by=actor.user_id,
            subscription_id=data.subscription_id,
            reason=data.reason,
        )
        replay_id = replay_row.id
    audit(db, request, 'event.replayed', 'events', row.id)
    return {
        'event_id': row.id,
        'target': data.target,
        'legacy_deliveries_created': len(legacy),
        'legacy_delivery_ids': [delivery.id for delivery in legacy],
        'replay_id': replay_id,
        'subscription_deliveries_created': len(dynamic),
        'subscription_delivery_ids': [delivery.id for delivery in dynamic],
    }


@router.get('/event-deliveries')
def event_deliveries(
    request: Request,
    kind: Literal['extension', 'webhook', 'all'] = 'all',
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(scoped_require('events.read')),
    db=Depends(get_db, scope='function'),
):
    items = []
    per_kind_offset = 0 if kind == 'all' else offset
    per_kind_limit = limit + offset if kind == 'all' else limit
    if kind in {'extension', 'all'}:
        query = select(ExtensionDelivery).join(
            EventContext, EventContext.event_sequence == ExtensionDelivery.event_sequence
        ).where(*_event_scope_filter(request))
        if status:
            query = query.where(ExtensionDelivery.status == status)
        rows = db.scalars(
            query.order_by(ExtensionDelivery.created_at.desc())
            .offset(per_kind_offset)
            .limit(per_kind_limit)
        ).all()
        items.extend(delivery_public(row, 'extension') for row in rows)
    if kind in {'webhook', 'all'}:
        query = (
            select(WebhookDelivery)
            .join(EventRecord, EventRecord.id == WebhookDelivery.event_id)
            .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
            .where(*_event_scope_filter(request))
        )
        if status:
            query = query.where(WebhookDelivery.status == status)
        rows = db.scalars(
            query.order_by(WebhookDelivery.created_at.desc())
            .offset(per_kind_offset)
            .limit(per_kind_limit)
        ).all()
        items.extend(delivery_public(row, 'webhook') for row in rows)
    items.sort(key=lambda item: item['created_at'], reverse=True)
    if kind == 'all':
        items = items[offset:offset + limit]
    return {'items': items[:limit]}


@router.get('/event-dead-letters')
def dead_letters(
    request: Request,
    limit: Limit = 100,
    actor=Depends(scoped_require('events.read')),
    db=Depends(get_db, scope='function'),
):
    extensions = db.scalars(
        select(ExtensionDelivery)
        .join(EventContext, EventContext.event_sequence == ExtensionDelivery.event_sequence)
        .where(
            ExtensionDelivery.status == 'dead_letter',
            *_event_scope_filter(request),
        )
        .order_by(ExtensionDelivery.created_at.desc())
        .limit(limit)
    ).all()
    webhooks = db.scalars(
        select(WebhookDelivery)
        .join(EventRecord, EventRecord.id == WebhookDelivery.event_id)
        .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
        .where(
            WebhookDelivery.status == 'failed',
            *_event_scope_filter(request),
        )
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    ).all()
    items = [delivery_public(row, 'extension') for row in extensions]
    items.extend(delivery_public(row, 'webhook') for row in webhooks)
    items.sort(key=lambda item: item['created_at'], reverse=True)
    return {'items': items[:limit]}


@router.post('/event-deliveries/{kind}/{delivery_id}/retry', status_code=202)
def retry_delivery(
    kind: Literal['extension', 'webhook'],
    delivery_id: str,
    request: Request,
    actor=Depends(scoped_require('events.replay')),
    db=Depends(get_db, scope='function'),
):
    if kind == 'extension':
        visible = db.scalar(
            select(ExtensionDelivery)
            .join(EventContext, EventContext.event_sequence == ExtensionDelivery.event_sequence)
            .where(ExtensionDelivery.id == delivery_id, *_event_scope_filter(request))
        )
    else:
        visible = db.scalar(
            select(WebhookDelivery)
            .join(EventRecord, EventRecord.id == WebhookDelivery.event_id)
            .join(EventContext, EventContext.event_sequence == EventRecord.sequence)
            .where(WebhookDelivery.id == delivery_id, *_event_scope_filter(request))
        )
    if visible is None:
        raise HTTPException(404, 'Delivery not found')
    try:
        row = retry_dead_letter(db, kind, delivery_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if row is None:
        raise HTTPException(404, 'Delivery not found')
    audit(db, request, 'event.delivery_retried', 'event_deliveries', delivery_id)
    return delivery_public(row, kind)


@router.get('/extensions')
def extensions(actor=Depends(global_require('extensions.read')), db=Depends(get_db, scope='function')):
    states = sync_extension_states(db)
    return {'items': [extension_public(states[spec.name], spec) for spec in extension_specs()]}


@router.get('/extensions/{name}')
def extension(name: str, actor=Depends(global_require('extensions.read')), db=Depends(get_db, scope='function')):
    spec = extension_by_name(name)
    if spec is None:
        raise HTTPException(404, 'Extension not found')
    states = sync_extension_states(db)
    return extension_public(states[name], spec)


@router.put('/extensions/{name}')
def update_extension(
    name: str,
    data: ExtensionUpdateInput,
    request: Request,
    actor=Depends(global_require('extensions.manage')),
    db=Depends(get_db, scope='function'),
):
    spec = extension_by_name(name)
    if spec is None:
        raise HTTPException(404, 'Extension not found')
    sync_extension_states(db)
    state = db.scalar(select(ExtensionState).where(ExtensionState.name == name).with_for_update())
    state.is_enabled = data.is_enabled
    state.config = data.config
    if data.is_enabled and state.status == 'disabled':
        state.status = 'healthy'
    if not data.is_enabled:
        state.status = 'disabled'
    state.updated_by = actor.user_id
    audit(db, request, 'extension.updated', 'extensions', name)
    return extension_public(state, spec)
