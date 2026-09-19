from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.database import get_db
from app.security.core import audit, require
from app.updates.service import UpdaterError, status_access_token, updater_request

router = APIRouter(prefix='/updates', tags=['updates'])


class UpdateRef(BaseModel):
    ref: str | None = None

    @field_validator('ref')
    @classmethod
    def valid_ref(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or len(value) > 200 or '..' in value or value.startswith('/'):
            raise ValueError('Invalid update ref')
        allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-')
        if any(char not in allowed for char in value):
            raise ValueError('Invalid update ref')
        return value


class UpdateSettings(BaseModel):
    enabled: bool
    interval_hours: Annotated[int, Field(ge=1, le=168)]
    ref: str

    @field_validator('ref')
    @classmethod
    def valid_ref(cls, value):
        checked = UpdateRef(ref=value)
        return checked.ref


def call(path: str, method: str = 'GET', payload: dict | None = None):
    try:
        return updater_request(path, method, payload)
    except UpdaterError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.get('/status')
def update_status(actor=Depends(require('updates.read'))):
    return call('/status')


@router.get('/status-access')
def update_status_access(actor=Depends(require('updates.read'))):
    try:
        return {'token': status_access_token()}
    except UpdaterError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.post('/check')
def update_check(
    data: UpdateRef,
    request: Request,
    actor=Depends(require('updates.read')),
    db=Depends(get_db, scope='function'),
):
    if data.ref is not None and 'updates.update' not in request.state.permissions:
        raise HTTPException(403, 'updates.update required to override update ref')
    result = call('/check', 'POST', data.model_dump(exclude_none=True))
    audit(db, request, 'update.checked', 'system_updates', data.ref or result.get('ref'))
    return result


@router.post('/run', status_code=202)
def update_run(
    data: UpdateRef,
    request: Request,
    actor=Depends(require('updates.execute')),
    db=Depends(get_db, scope='function'),
):
    if data.ref is not None and 'updates.update' not in request.state.permissions:
        raise HTTPException(403, 'updates.update required to override update ref')
    result = call('/run', 'POST', data.model_dump(exclude_none=True))
    audit(db, request, 'update.started', 'system_updates', data.ref)
    return result


@router.get('/settings')
def update_settings(actor=Depends(require('updates.read'))):
    return call('/settings')


@router.put('/settings')
def update_settings_write(
    data: UpdateSettings,
    request: Request,
    actor=Depends(require('updates.update')),
    db=Depends(get_db, scope='function'),
):
    result = call('/settings', 'POST', data.model_dump())
    audit(db, request, 'update.settings_changed', 'system_updates', result.get('ref'))
    return result
