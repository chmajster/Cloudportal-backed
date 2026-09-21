from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.api.common import Limit, Offset, idempotent
from app.api.proxmox_management import console_session as proxmox_console_session
from app.database import get_db
from app.day2.errors import Day2Failure, failure
from app.day2.models import BulkDay2ActionRequest, Day2ActionRequest
from app.day2.providers import day2_provider
from app.day2.registry import get_action
from app.day2.schemas import Day2BulkInput, Day2ExecuteInput, Day2ResourceStateInput, Day2SettingsInput
from app.day2.service import (
    action_catalog,
    approve_action,
    cancel_action,
    create_action,
    day2_settings,
    get_resource_state,
    list_action_requests,
    public_action_request,
    resolve_target,
    resource_state_public,
    save_day2_settings,
)
from app.models import Job, now
from app.security.core import audit, require


router = APIRouter(tags=['day2'])


def _raise(error):
    if isinstance(error, Day2Failure):
        raise error.http() from None
    raise error


def _permissions(request):
    return set(getattr(request.state, 'permissions', set()))


def _owned_action(db, id, actor, request):
    row = db.get(Day2ActionRequest, id)
    if row is None:
        raise HTTPException(404, 'Day-2 action not found')
    if row.requested_by != actor.user_id and 'day2.admin' not in _permissions(request):
        raise HTTPException(404, 'Day-2 action not found')
    return row


@router.get('/resources/{resource_id}/actions')
def resource_actions(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                     db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        return action_catalog(db, target, credential, _permissions(request))
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/actions/{action_id}')
def resource_action(resource_id: str, action_id: str, request: Request, actor=Depends(require('day2.view')),
                    db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        catalog = action_catalog(db, target, credential, _permissions(request))
        item = next((value for value in catalog['actions'] if value['id'] == action_id.lower()), None)
        if item is None:
            get_action(action_id)
            raise failure('PERMISSION_DENIED', status_code=403)
        return {**item, 'resource_id': resource_id, 'management_mode': target.management_mode}
    except Day2Failure as error:
        _raise(error)


@router.post('/resources/{resource_id}/actions/{action_id}/validate')
def validate_resource_action(resource_id: str, action_id: str, data: Day2ExecuteInput, request: Request,
                             actor=Depends(require('day2.view')), db=Depends(get_db, scope='function')):
    from app.day2.service import validate_action
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        return validate_action(db, target, credential, action_id, data.parameters, data.reason, _permissions(request))
    except Day2Failure as error:
        _raise(error)


@router.post('/resources/{resource_id}/actions/{action_id}', status_code=202)
def execute_resource_action(resource_id: str, action_id: str, data: Day2ExecuteInput, request: Request,
                            actor=Depends(require('day2.view')), db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        payload = {'resource_id': resource_id, 'action': action_id.lower(), **data.model_dump(mode='json')}

        def create():
            row, validation = create_action(
                db, request, actor, target, credential, action_id, data.parameters, data.reason, _permissions(request)
            )
            return {
                'action_request_id': row.id,
                'job_id': row.job_id,
                'state': row.status.lower(),
                'approval_required': validation['approval_required'],
                'changes': validation['changes'],
                'restart_required': validation['restart_required'],
                'warnings': validation['warnings'],
            }

        return idempotent(db, request, actor, payload, create, required=True)
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/capabilities')
def resource_capabilities(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                          db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        adapter = day2_provider(credential)
        catalog = action_catalog(db, target, credential, _permissions(request))
        return {
            'resource_id': resource_id,
            'provider': target.provider_type,
            'management_mode': target.management_mode,
            'capabilities': adapter.capabilities(target),
            'power_state': catalog['power_state'],
        }
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/day2-state')
def resource_day2_state(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                        db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        state = get_resource_state(db, resource_id)
        adapter = day2_provider(credential)
        live = adapter.snapshot_state(target)
        return {**resource_state_public(state), 'live': live, 'management_mode': target.management_mode}
    except Day2Failure as error:
        _raise(error)


@router.put('/resources/{resource_id}/day2-state')
def update_resource_day2_state(resource_id: str, data: Day2ResourceStateInput, request: Request,
                               actor=Depends(require('day2.view')), db=Depends(get_db, scope='function')):
    from app.day2.service import get_resource_state
    try:
        resolve_target(db, resource_id, request, actor)
        permissions = _permissions(request)
        if data.protected is not None and 'day2.admin' not in permissions:
            raise failure('PERMISSION_DENIED', status_code=403, details={'permission': 'day2.admin'})
        if data.platform_metadata is not None and 'day2.metadata.manage' not in permissions:
            raise failure('PERMISSION_DENIED', status_code=403, details={'permission': 'day2.metadata.manage'})
        state = get_resource_state(db, resource_id, create=True)
        if data.protected is not None:
            state.protected = data.protected
        if data.platform_metadata is not None:
            state.platform_metadata = dict(data.platform_metadata)
        audit(db, request, 'day2.resource_state.updated', 'managed_resources', resource_id)
        return resource_state_public(state)
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/snapshots')
def resource_snapshots(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                       db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        adapter = day2_provider(credential)
        if not hasattr(adapter, 'snapshots'):
            raise failure('ACTION_NOT_SUPPORTED')
        return {'items': adapter.snapshots(target)}
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/disks')
def resource_disks(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                   db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        adapter = day2_provider(credential)
        if not hasattr(adapter, 'disks'):
            raise failure('ACTION_NOT_SUPPORTED')
        return {'items': adapter.disks(target)}
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/nics')
def resource_nics(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                  db=Depends(get_db, scope='function')):
    try:
        target, credential, _ = resolve_target(db, resource_id, request, actor)
        adapter = day2_provider(credential)
        if not hasattr(adapter, 'nics'):
            raise failure('ACTION_NOT_SUPPORTED')
        return {'items': adapter.nics(target)}
    except Day2Failure as error:
        _raise(error)


@router.post('/resources/{resource_id}/console')
def resource_console(resource_id: str, request: Request, actor=Depends(require('day2.view')),
                     db=Depends(get_db, scope='function')):
    try:
        permissions = _permissions(request)
        if 'vms.console' not in permissions:
            raise failure('PERMISSION_DENIED', status_code=403, details={'permission': 'vms.console'})
        target, _, _ = resolve_target(db, resource_id, request, actor)
        if target.provider_type != 'proxmox' or target.node is None or target.vm_id is None:
            raise failure('ACTION_NOT_SUPPORTED', message='Console is not available for this provider resource')
        return proxmox_console_session(target.provider_id, target.node, target.vm_id, request, actor, db)
    except Day2Failure as error:
        _raise(error)


@router.get('/resources/{resource_id}/day2-actions')
def resource_action_history(resource_id: str, request: Request, limit: Limit = 100, offset: Offset = 0,
                            actor=Depends(require('day2.view')), db=Depends(get_db, scope='function')):
    try:
        resolve_target(db, resource_id, request, actor)
        return {'items': [public_action_request(row) for row in list_action_requests(db, resource_id, limit, offset)]}
    except Day2Failure as error:
        _raise(error)


@router.get('/day2-actions')
def day2_actions(request: Request, limit: Limit = 100, offset: Offset = 0,
                 actor=Depends(require('day2.view')), db=Depends(get_db, scope='function')):
    query = select(Day2ActionRequest)
    if 'day2.admin' not in _permissions(request):
        query = query.where(Day2ActionRequest.requested_by == actor.user_id)
    rows = db.scalars(query.order_by(Day2ActionRequest.requested_at.desc()).offset(offset).limit(limit)).all()
    return {'items': [public_action_request(row) for row in rows]}


@router.get('/day2-actions/{id}')
def day2_action(id: str, request: Request, actor=Depends(require('day2.view')),
                db=Depends(get_db, scope='function')):
    return public_action_request(_owned_action(db, id, actor, request))


@router.post('/day2-actions/{id}/cancel')
def cancel_day2_action(id: str, request: Request, actor=Depends(require('day2.cancel')),
                       db=Depends(get_db, scope='function')):
    try:
        row = _owned_action(db, id, actor, request)
        return public_action_request(cancel_action(db, request, actor, row))
    except Day2Failure as error:
        _raise(error)


@router.post('/day2-actions/{id}/approve')
def approve_day2_action(id: str, request: Request, actor=Depends(require('day2.approve')),
                        db=Depends(get_db, scope='function')):
    try:
        row = db.get(Day2ActionRequest, id)
        if row is None:
            raise failure('RESOURCE_NOT_FOUND', message='Day-2 action not found', status_code=404)
        return public_action_request(approve_action(db, request, actor, row, _permissions(request)))
    except Day2Failure as error:
        _raise(error)


@router.post('/day2-actions/{id}/retry', status_code=202)
def retry_day2_action(id: str, request: Request, actor=Depends(require('day2.retry')),
                      db=Depends(get_db, scope='function')):
    try:
        previous = _owned_action(db, id, actor, request)
        if previous.status != 'FAILED':
            raise failure('INVALID_STATE', message='Only failed Day-2 actions can be retried')
        target, credential, _ = resolve_target(db, previous.resource_id, request, actor)
        payload = {'retry_of': previous.id, 'attempt': previous.attempt + 1}

        def create():
            row, validation = create_action(
                db, request, actor, target, credential, previous.action, previous.parameters or {}, previous.reason,
                _permissions(request), retry_of=previous.id, attempt=previous.attempt + 1,
            )
            return {
                'action_request_id': row.id,
                'job_id': row.job_id,
                'state': row.status.lower(),
                'approval_required': validation['approval_required'],
                'retry_of': previous.id,
                'attempt': row.attempt,
            }

        return idempotent(db, request, actor, payload, create, required=True)
    except Day2Failure as error:
        _raise(error)


@router.post('/day2-actions/bulk', status_code=202)
def bulk_day2_actions(data: Day2BulkInput, request: Request, actor=Depends(require('day2.view')),
                      db=Depends(get_db, scope='function')):
    try:
        config = day2_settings(db)
        resource_ids = list(dict.fromkeys(data.resource_ids))
        if len(resource_ids) > config['max_bulk_action_size']:
            raise failure('VALIDATION_FAILED', message='Bulk action exceeds the configured maximum size', status_code=422)
        payload = data.model_dump(mode='json')

        def create():
            bulk = BulkDay2ActionRequest(
                action=data.action.lower(), resource_ids=resource_ids, requested_by=actor.user_id,
                status='QUEUED', request_id=str(getattr(request.state, 'request_id', '') or ''),
            )
            db.add(bulk)
            db.flush()
            children, errors = [], []
            waiting = False
            for resource_id in resource_ids:
                try:
                    target, credential, _ = resolve_target(db, resource_id, request, actor)
                    child, validation = create_action(
                        db, request, actor, target, credential, data.action, data.parameters, data.reason, _permissions(request)
                    )
                    waiting = waiting or validation['approval_required']
                    children.append({'resource_id': resource_id, 'action_request_id': child.id, 'job_id': child.job_id, 'status': child.status})
                except Day2Failure as error:
                    errors.append({'resource_id': resource_id, 'code': error.code, 'message': error.message})
            bulk.status = 'WAITING_APPROVAL' if waiting else ('PARTIAL' if errors else 'QUEUED')
            bulk.result = {'children': children, 'errors': errors}
            audit(db, request, 'day2.bulk.requested', 'bulk_day2_actions', bulk.id)
            return {'id': bulk.id, 'status': bulk.status, **bulk.result}

        return idempotent(db, request, actor, payload, create, required=True)
    except Day2Failure as error:
        _raise(error)


@router.get('/day2/settings')
def get_day2_settings(request: Request, actor=Depends(require('settings.read')),
                      db=Depends(get_db, scope='function')):
    if 'day2.admin' not in _permissions(request):
        raise HTTPException(403, 'Permission required: day2.admin')
    return day2_settings(db)


@router.put('/day2/settings')
def put_day2_settings(data: Day2SettingsInput, request: Request, actor=Depends(require('settings.update')),
                      db=Depends(get_db, scope='function')):
    if 'day2.admin' not in _permissions(request):
        raise HTTPException(403, 'Permission required: day2.admin')
    result = save_day2_settings(db, data.model_dump(mode='json'))
    audit(db, request, 'day2.settings.updated', 'settings', 'day2')
    return result
