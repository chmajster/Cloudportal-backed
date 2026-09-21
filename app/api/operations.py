from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.common import Limit, Offset, find, idempotent
from app.api.schemas import ScheduledOperationInput, WebhookEndpointInput
from app.database import get_db
from app.jobs.lifecycle import has_released_allocations
from app.models import Deployment, ScheduledOperation, WebhookDelivery, WebhookEndpoint, now
from app.operations.service import (
    ensure_operation_permissions,
    new_webhook_secret,
    validate_webhook_url,
)
from app.security.core import audit
from app.resource_scope.http import require


router = APIRouter(tags=['operations'])
SCHEDULE_FIELDS = (
    'id name deployment_id operation next_run_at interval_seconds is_active '
    'created_by last_run_at last_error created_at updated_at'
)
WEBHOOK_FIELDS = 'id name url events is_active created_by created_at updated_at'
DELIVERY_FIELDS = (
    'id endpoint_id event resource_id status attempts next_attempt_at delivered_at '
    'last_error created_at updated_at'
)


def public(row, fields):
    return {field: getattr(row, field) for field in fields.split()}


def validate_schedule_target(db, request, data):
    deployment = find(db, Deployment, data.deployment_id)
    if deployment.status == 'destroyed':
        raise HTTPException(409, 'Cannot schedule an operation for a destroyed deployment')
    if data.operation == 'terraform.apply' and has_released_allocations(db, deployment.id):
        raise HTTPException(409, 'Deployment allocations were released; execute the Blueprint again')
    if data.operation == 'terraform.apply' and ((deployment.workflow or {}).get('adoption') or {}).get('plan_only'):
        raise HTTPException(409, 'Adopted deployment is plan-only; terraform.apply is disabled')
    ensure_operation_permissions(request.state.permissions, data.operation, deployment)
    return deployment


@router.get('/schedules')
def schedules(limit: Limit = 100, offset: Offset = 0,
              active: Annotated[bool | None, Query()] = None,
              actor=Depends(require('schedules.read')), db=Depends(get_db, scope='function')):
    query = select(ScheduledOperation)
    if active is not None:
        query = query.where(ScheduledOperation.is_active.is_(active))
    rows = db.scalars(
        query.order_by(ScheduledOperation.next_run_at).offset(offset).limit(limit)
    ).all()
    return {'items': [public(row, SCHEDULE_FIELDS) for row in rows]}


@router.get('/schedules/{id}')
def schedule(id: str, actor=Depends(require('schedules.read')), db=Depends(get_db, scope='function')):
    return public(find(db, ScheduledOperation, id), SCHEDULE_FIELDS)


@router.post('/schedules', status_code=201)
def create_schedule(data: ScheduledOperationInput, request: Request,
                    actor=Depends(require('schedules.create')), db=Depends(get_db, scope='function')):
    validate_schedule_target(db, request, data)

    def create():
        row = ScheduledOperation(
            name=data.name,
            deployment_id=data.deployment_id,
            operation=data.operation,
            next_run_at=data.next_run_at,
            interval_seconds=data.interval_seconds,
            created_by=actor.user_id,
            token_id=None,
        )
        db.add(row)
        db.flush()
        audit(db, request, 'schedule.created', 'schedules', row.id)
        return public(row, SCHEDULE_FIELDS)

    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/schedules/{id}')
def update_schedule(id: str, data: ScheduledOperationInput, request: Request,
                    actor=Depends(require('schedules.update')), db=Depends(get_db, scope='function')):
    validate_schedule_target(db, request, data)
    row = db.scalar(select(ScheduledOperation).where(ScheduledOperation.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    for key in ('name', 'deployment_id', 'operation', 'next_run_at', 'interval_seconds'):
        setattr(row, key, getattr(data, key))
    row.is_active = True
    row.last_error = None
    audit(db, request, 'schedule.updated', 'schedules', row.id)
    return public(row, SCHEDULE_FIELDS)


@router.post('/schedules/{id}/disable')
def disable_schedule(id: str, request: Request,
                     actor=Depends(require('schedules.update')), db=Depends(get_db, scope='function')):
    row = db.scalar(select(ScheduledOperation).where(ScheduledOperation.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    row.is_active = False
    audit(db, request, 'schedule.disabled', 'schedules', row.id)
    return public(row, SCHEDULE_FIELDS)


@router.delete('/schedules/{id}')
def delete_schedule(id: str, request: Request,
                    actor=Depends(require('schedules.delete')), db=Depends(get_db, scope='function')):
    row = find(db, ScheduledOperation, id)
    db.delete(row)
    audit(db, request, 'schedule.deleted', 'schedules', id)
    return {'deleted': True}


@router.get('/webhooks')
def webhooks(limit: Limit = 100, offset: Offset = 0,
             actor=Depends(require('webhooks.read')), db=Depends(get_db, scope='function')):
    rows = db.scalars(
        select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {'items': [public(row, WEBHOOK_FIELDS) for row in rows]}


@router.post('/webhooks', status_code=201)
def create_webhook(data: WebhookEndpointInput, request: Request,
                   actor=Depends(require('webhooks.create')), db=Depends(get_db, scope='function')):
    validate_webhook_url(data.url)

    def create():
        import uuid
        endpoint_id = str(uuid.uuid4())
        plain, encrypted = new_webhook_secret(endpoint_id)
        row = WebhookEndpoint(
            id=endpoint_id,
            name=data.name,
            url=data.url,
            events=sorted(set(data.events)),
            encrypted_secret=encrypted,
            is_active=data.is_active,
            created_by=actor.user_id,
        )
        db.add(row)
        db.flush()
        audit(db, request, 'webhook.created', 'webhooks', row.id)
        return {**public(row, WEBHOOK_FIELDS), 'secret': plain}

    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/webhooks/{id}')
def update_webhook(id: str, data: WebhookEndpointInput, request: Request,
                   actor=Depends(require('webhooks.update')), db=Depends(get_db, scope='function')):
    validate_webhook_url(data.url)
    row = db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    row.name = data.name
    row.url = data.url
    row.events = sorted(set(data.events))
    row.is_active = data.is_active
    audit(db, request, 'webhook.updated', 'webhooks', row.id)
    return public(row, WEBHOOK_FIELDS)


@router.post('/webhooks/{id}/rotate-secret')
def rotate_webhook_secret(id: str, request: Request,
                          actor=Depends(require('webhooks.update')), db=Depends(get_db, scope='function')):
    row = db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    plain, encrypted = new_webhook_secret(row.id)
    row.encrypted_secret = encrypted
    audit(db, request, 'webhook.secret_rotated', 'webhooks', row.id)
    return {'id': row.id, 'secret': plain}


@router.delete('/webhooks/{id}')
def delete_webhook(id: str, request: Request,
                   actor=Depends(require('webhooks.delete')), db=Depends(get_db, scope='function')):
    row = find(db, WebhookEndpoint, id)
    db.delete(row)
    audit(db, request, 'webhook.deleted', 'webhooks', id)
    return {'deleted': True}


@router.get('/webhook-deliveries')
def webhook_deliveries(endpoint_id: Annotated[str | None, Query(max_length=36)] = None,
                       status: Annotated[Literal['pending', 'delivered', 'failed'] | None, Query()] = None,
                       limit: Limit = 100, offset: Offset = 0,
                       actor=Depends(require('webhooks.read')), db=Depends(get_db, scope='function')):
    query = select(WebhookDelivery)
    if endpoint_id:
        query = query.where(WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        query = query.where(WebhookDelivery.status == status)
    rows = db.scalars(
        query.order_by(WebhookDelivery.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {'items': [public(row, DELIVERY_FIELDS) for row in rows]}
