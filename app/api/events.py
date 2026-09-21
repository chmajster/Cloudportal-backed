import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.common import Limit, Offset, idempotent
from app.database import get_db
from app.events.registry import extension_by_name, extension_specs, hook_names
from app.events.service import (
    broker_stats,
    event_public,
    extension_public,
    publish_event,
    replay_event,
    retry_dead_letter,
    sync_extension_states,
)
from app.models import EventRecord, ExtensionDelivery, ExtensionState, WebhookDelivery
from app.security.core import audit, require


router = APIRouter(tags=['events'])


class EventPublishInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Annotated[str, Field(pattern=r'^custom\.[a-z0-9][a-z0-9_.-]{0,119}$')]
    payload: dict = Field(default_factory=dict)
    subject_type: Annotated[str, Field(min_length=1, max_length=64)] = 'custom'
    subject_id: Annotated[str, Field(max_length=255)] = ''
    schema_version: int = Field(default=1, ge=1, le=1000)
    correlation_id: Annotated[str | None, Field(max_length=64)] = None
    causation_id: Annotated[str | None, Field(max_length=64)] = None
    metadata: dict = Field(default_factory=dict)


class EventReplayInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Literal['all', 'webhooks', 'extensions'] = 'all'


class ExtensionUpdateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_enabled: bool
    config: dict = Field(default_factory=dict)


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
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    subject_type: Annotated[str | None, Query(max_length=64)] = None,
    subject_id: Annotated[str | None, Query(max_length=255)] = None,
    correlation_id: Annotated[str | None, Query(max_length=64)] = None,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    query = select(EventRecord)
    if event_type:
        query = query.where(EventRecord.type == event_type)
    if subject_type:
        query = query.where(EventRecord.subject_type == subject_type)
    if subject_id:
        query = query.where(EventRecord.subject_id == subject_id)
    if correlation_id:
        query = query.where(EventRecord.correlation_id == correlation_id)
    if after_sequence is not None:
        query = query.where(EventRecord.sequence > after_sequence)
    rows = db.scalars(
        query.order_by(EventRecord.sequence.desc()).offset(offset).limit(limit)
    ).all()
    return {'items': [event_public(row) for row in rows]}


@router.get('/events/stats')
def event_stats(actor=Depends(require('events.read')), db=Depends(get_db, scope='function')):
    return broker_stats(db)


@router.get('/events/types')
def event_types(actor=Depends(require('events.read')), db=Depends(get_db, scope='function')):
    persisted = db.scalars(select(EventRecord.type).distinct().order_by(EventRecord.type)).all()
    return {
        'persisted': list(persisted),
        'extension_patterns': sorted({pattern for spec in extension_specs() for pattern in spec.event_patterns}),
        'hooks': list(hook_names()),
    }


@router.get('/events/{event_id}')
def event(event_id: str, actor=Depends(require('events.read')), db=Depends(get_db, scope='function')):
    row = db.scalar(select(EventRecord).where(EventRecord.id == event_id))
    if row is None:
        raise HTTPException(404, 'Event not found')
    return event_public(row)


@router.post('/events', status_code=201)
def create_event(
    data: EventPublishInput,
    request: Request,
    actor=Depends(require('events.publish')),
    db=Depends(get_db, scope='function'),
):
    if len(json.dumps(data.payload, separators=(',', ':'), default=str).encode()) > 256 * 1024:
        raise HTTPException(413, 'Event payload exceeds 256 KiB')

    def create():
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
        )
        audit(db, request, 'event.published', 'events', row.id)
        return event_public(row)

    return idempotent(db, request, actor, data.model_dump(mode='json'), create, required=True)


@router.post('/events/{event_id}/replay', status_code=202)
def replay(
    event_id: str,
    data: EventReplayInput,
    request: Request,
    actor=Depends(require('events.replay')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(EventRecord).where(EventRecord.id == event_id))
    if row is None:
        raise HTTPException(404, 'Event not found')
    deliveries = replay_event(db, row, data.target)
    audit(db, request, 'event.replayed', 'events', row.id)
    return {
        'event_id': row.id,
        'target': data.target,
        'deliveries_created': len(deliveries),
        'delivery_ids': [delivery.id for delivery in deliveries],
    }


@router.get('/event-deliveries')
def event_deliveries(
    kind: Literal['extension', 'webhook', 'all'] = 'all',
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    items = []
    if kind in {'extension', 'all'}:
        query = select(ExtensionDelivery)
        if status:
            query = query.where(ExtensionDelivery.status == status)
        rows = db.scalars(query.order_by(ExtensionDelivery.created_at.desc()).offset(offset).limit(limit)).all()
        items.extend(delivery_public(row, 'extension') for row in rows)
    if kind in {'webhook', 'all'}:
        query = select(WebhookDelivery)
        if status:
            query = query.where(WebhookDelivery.status == status)
        rows = db.scalars(query.order_by(WebhookDelivery.created_at.desc()).offset(offset).limit(limit)).all()
        items.extend(delivery_public(row, 'webhook') for row in rows)
    items.sort(key=lambda item: item['created_at'], reverse=True)
    return {'items': items[:limit]}


@router.get('/event-dead-letters')
def dead_letters(
    limit: Limit = 100,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    extensions = db.scalars(
        select(ExtensionDelivery)
        .where(ExtensionDelivery.status == 'dead_letter')
        .order_by(ExtensionDelivery.created_at.desc())
        .limit(limit)
    ).all()
    webhooks = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.status == 'failed')
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
    actor=Depends(require('events.replay')),
    db=Depends(get_db, scope='function'),
):
    try:
        row = retry_dead_letter(db, kind, delivery_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if row is None:
        raise HTTPException(404, 'Delivery not found')
    audit(db, request, 'event.delivery_retried', 'event_deliveries', delivery_id)
    return delivery_public(row, kind)


@router.get('/extensions')
def extensions(actor=Depends(require('extensions.read')), db=Depends(get_db, scope='function')):
    states = sync_extension_states(db)
    return {'items': [extension_public(states[spec.name], spec) for spec in extension_specs()]}


@router.get('/extensions/{name}')
def extension(name: str, actor=Depends(require('extensions.read')), db=Depends(get_db, scope='function')):
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
    actor=Depends(require('extensions.manage')),
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
