import json
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.common import Limit, Offset, find, idempotent
from app.database import get_db
from app.events.consumers import (
    ack_consumer,
    consumer_public,
    poll_consumer,
    reset_consumer,
    resolve_start_sequence,
)
from app.events.registry import event_matches
from app.events.schemas import (
    EventSchemaValidationError,
    schema_public,
    validate_schema_document,
)
from app.models import EventConsumer, EventRecord, EventSchema, User
from app.security.core import audit, effective_permissions, require


router = APIRouter(tags=['event-enterprise'])
EVENT_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$')


def normalize_patterns(values: list[str]) -> list[str]:
    result = []
    for raw in values:
        value = str(raw).strip().lower()
        valid = (
            value == '*'
            or (len(value) <= 128 and bool(EVENT_NAME_RE.fullmatch(value)))
            or (
                value.endswith('.*')
                and len(value) <= 128
                and bool(EVENT_NAME_RE.fullmatch(value[:-2] + '.placeholder'))
            )
        )
        if not valid:
            raise ValueError('Invalid event pattern')
        if value not in result:
            result.append(value)
    return result


class EventSchemaInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_type: Annotated[str, Field(min_length=3, max_length=128)]
    version: int = Field(ge=1, le=1000000)
    schema: dict
    compatibility: Literal['none'] = 'none'
    is_active: bool = True

    @field_validator('event_type')
    @classmethod
    def valid_event_type(cls, value):
        normalized = value.strip().lower()
        if not EVENT_NAME_RE.fullmatch(normalized):
            raise ValueError('Invalid event type')
        return normalized


class EventSchemaStateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_active: bool


class EventSchemaValidateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    payload: dict


class EventConsumerInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Annotated[str, Field(min_length=1, max_length=100)]
    event_patterns: Annotated[list[str], Field(min_length=1, max_length=64)]
    max_batch: int = Field(default=100, ge=1, le=500)
    is_active: bool = True
    owner_user_id: int | None = Field(default=None, gt=0)
    start_from: Literal['latest', 'earliest', 'sequence'] = 'latest'
    start_sequence: int | None = Field(default=None, ge=0)

    @field_validator('event_patterns')
    @classmethod
    def patterns(cls, value):
        return normalize_patterns(value)

    @model_validator(mode='after')
    def start_position(self):
        if self.start_from == 'sequence' and self.start_sequence is None:
            raise ValueError('start_sequence is required for start_from=sequence')
        if self.start_from != 'sequence' and self.start_sequence is not None:
            raise ValueError('start_sequence is only valid with start_from=sequence')
        return self


class EventConsumerUpdateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Annotated[str, Field(min_length=1, max_length=100)]
    event_patterns: Annotated[list[str], Field(min_length=1, max_length=64)]
    max_batch: int = Field(default=100, ge=1, le=500)
    is_active: bool = True
    owner_user_id: int = Field(gt=0)

    @field_validator('event_patterns')
    @classmethod
    def patterns(cls, value):
        return normalize_patterns(value)


class EventConsumerAckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence: int = Field(ge=0)


class EventConsumerResetInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_from: Literal['latest', 'earliest', 'sequence']
    sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def coherent(self):
        if self.start_from == 'sequence' and self.sequence is None:
            raise ValueError('sequence is required for start_from=sequence')
        if self.start_from != 'sequence' and self.sequence is not None:
            raise ValueError('sequence is only valid with start_from=sequence')
        return self


def ensure_consumer_access(request, actor, row: EventConsumer):
    if row.owner_user_id != actor.user_id and 'events.manage' not in request.state.permissions:
        raise HTTPException(404, 'Event consumer not found')
    return row


def ensure_owner(db, user_id: int):
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.is_locked:
        raise HTTPException(422, 'Consumer owner must be an active unlocked user')
    if 'events.consume' not in effective_permissions(user):
        raise HTTPException(422, 'Consumer owner must have events.consume permission')
    return user


@router.get('/event-schemas')
def event_schemas(
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    active: Annotated[bool | None, Query()] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    query = select(EventSchema)
    if event_type:
        query = query.where(EventSchema.event_type == event_type.strip().lower())
    if active is not None:
        query = query.where(EventSchema.is_active.is_(active))
    rows = db.scalars(
        query.order_by(EventSchema.event_type, EventSchema.version.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {'items': [schema_public(row) for row in rows]}


@router.get('/event-schemas/{schema_id}')
def event_schema(
    schema_id: str,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    return schema_public(find(db, EventSchema, schema_id))


@router.post('/event-schemas', status_code=201)
def create_event_schema(
    data: EventSchemaInput,
    request: Request,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key')],
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    encoded = json.dumps(data.schema, separators=(',', ':'), sort_keys=True).encode()
    if len(encoded) > 256 * 1024:
        raise HTTPException(413, 'Event schema exceeds 256 KiB')
    try:
        validate_schema_document(data.schema)
    except EventSchemaValidationError as exc:
        raise HTTPException(422, str(exc)) from None

    def create():
        row = EventSchema(
            id=str(uuid.uuid4()),
            event_type=data.event_type,
            version=data.version,
            schema_json=data.schema,
            compatibility=data.compatibility,
            is_active=data.is_active,
            created_by=actor.user_id,
        )
        db.add(row)
        db.flush()
        audit(db, request, 'event_schema.created', 'event_schemas', row.id)
        return schema_public(row)

    return idempotent(db, request, actor, data.model_dump(mode='json'), create, required=True)


@router.put('/event-schemas/{schema_id}/state')
def update_event_schema_state(
    schema_id: str,
    data: EventSchemaStateInput,
    request: Request,
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(EventSchema).where(EventSchema.id == schema_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Event schema not found')
    row.is_active = data.is_active
    audit(db, request, 'event_schema.state_updated', 'event_schemas', row.id)
    return schema_public(row)


@router.post('/event-schemas/{schema_id}/validate')
def validate_against_event_schema(
    schema_id: str,
    data: EventSchemaValidateInput,
    actor=Depends(require('events.read')),
    db=Depends(get_db, scope='function'),
):
    row = find(db, EventSchema, schema_id)
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError

    try:
        Draft202012Validator(row.schema_json).validate(data.payload)
    except ValidationError as exc:
        path = '.'.join(str(part) for part in exc.absolute_path)
        return {
            'valid': False,
            'path': path,
            'message': exc.message[:500],
        }
    return {'valid': True, 'path': '', 'message': ''}


@router.get('/event-consumers')
def event_consumers(
    request: Request,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('events.consume')),
    db=Depends(get_db, scope='function'),
):
    query = select(EventConsumer)
    if 'events.manage' not in request.state.permissions:
        query = query.where(EventConsumer.owner_user_id == actor.user_id)
    rows = db.scalars(
        query.order_by(EventConsumer.name).offset(offset).limit(limit)
    ).all()
    return {'items': [consumer_public(db, row) for row in rows]}


@router.get('/event-consumers/{consumer_id}')
def event_consumer(
    consumer_id: str,
    request: Request,
    actor=Depends(require('events.consume')),
    db=Depends(get_db, scope='function'),
):
    row = find(db, EventConsumer, consumer_id)
    ensure_consumer_access(request, actor, row)
    return consumer_public(db, row)


@router.post('/event-consumers', status_code=201)
def create_event_consumer(
    data: EventConsumerInput,
    request: Request,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key')],
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    owner_id = data.owner_user_id or actor.user_id
    ensure_owner(db, owner_id)
    try:
        cursor = resolve_start_sequence(db, data.start_from, data.start_sequence)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    def create():
        row = EventConsumer(
            id=str(uuid.uuid4()),
            name=data.name,
            event_patterns=data.event_patterns,
            cursor_sequence=cursor,
            max_batch=data.max_batch,
            is_active=data.is_active,
            owner_user_id=owner_id,
            created_by=actor.user_id,
        )
        db.add(row)
        db.flush()
        audit(db, request, 'event_consumer.created', 'event_consumers', row.id)
        return consumer_public(db, row)

    return idempotent(db, request, actor, data.model_dump(mode='json'), create, required=True)


@router.put('/event-consumers/{consumer_id}')
def update_event_consumer(
    consumer_id: str,
    data: EventConsumerUpdateInput,
    request: Request,
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    ensure_owner(db, data.owner_user_id)
    row = db.scalar(select(EventConsumer).where(EventConsumer.id == consumer_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Event consumer not found')
    row.name = data.name
    row.event_patterns = data.event_patterns
    row.max_batch = data.max_batch
    row.is_active = data.is_active
    row.owner_user_id = data.owner_user_id
    audit(db, request, 'event_consumer.updated', 'event_consumers', row.id)
    return consumer_public(db, row)


@router.delete('/event-consumers/{consumer_id}')
def delete_event_consumer(
    consumer_id: str,
    request: Request,
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    row = find(db, EventConsumer, consumer_id)
    db.delete(row)
    audit(db, request, 'event_consumer.deleted', 'event_consumers', consumer_id)
    return {'deleted': True}


@router.get('/event-consumers/{consumer_id}/events')
def poll_event_consumer(
    consumer_id: str,
    request: Request,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    actor=Depends(require('events.consume')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(EventConsumer).where(EventConsumer.id == consumer_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Event consumer not found')
    ensure_consumer_access(request, actor, row)
    try:
        return poll_consumer(db, row, limit)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/event-consumers/{consumer_id}/ack')
def ack_event_consumer(
    consumer_id: str,
    data: EventConsumerAckInput,
    request: Request,
    actor=Depends(require('events.consume')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(EventConsumer).where(EventConsumer.id == consumer_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Event consumer not found')
    ensure_consumer_access(request, actor, row)
    try:
        ack_consumer(db, row, data.sequence)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, request, 'event_consumer.acked', 'event_consumers', row.id)
    return consumer_public(db, row)


@router.post('/event-consumers/{consumer_id}/reset')
def reset_event_consumer(
    consumer_id: str,
    data: EventConsumerResetInput,
    request: Request,
    actor=Depends(require('events.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(EventConsumer).where(EventConsumer.id == consumer_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Event consumer not found')
    try:
        reset_consumer(db, row, data.start_from, data.sequence)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, request, 'event_consumer.reset', 'event_consumers', row.id)
    return consumer_public(db, row)
