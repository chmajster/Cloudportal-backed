from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app.security.core import audit, require
from app.updates.service import UpdaterError, updater_request


router = APIRouter(prefix='/settings/tls', tags=['tls-settings'])


class TLSCustomInput(BaseModel):
    certificate_pem: str = Field(min_length=64, max_length=65536)
    private_key_pem: str = Field(min_length=64, max_length=32768, json_schema_extra={'writeOnly': True})
    hostname: str | None = Field(default=None, min_length=1, max_length=253)


class TLSLetsEncryptInput(BaseModel):
    lineage: str = Field(min_length=1, max_length=253)
    hostname: str | None = Field(default=None, min_length=1, max_length=253)


class TLSSelfSignedInput(BaseModel):
    hostname: str | None = Field(default=None, min_length=1, max_length=253)


def call(path: str, method: str = 'GET', payload: dict | None = None):
    try:
        return updater_request(path, method, payload, timeout=60)
    except UpdaterError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.get('')
def tls_status(actor=Depends(require('settings.read'))):
    return call('/tls/status')


@router.get('/letsencrypt')
def tls_letsencrypt_candidates(actor=Depends(require('settings.read'))):
    return call('/tls/letsencrypt')


@router.post('/custom')
def tls_custom(
    data: TLSCustomInput,
    request: Request,
    actor=Depends(require('settings.update')),
    db=Depends(get_db, scope='function'),
):
    result = call('/tls/custom', 'POST', data.model_dump())
    audit(db, request, 'settings.tls.custom', 'system_tls', result.get('hostname'))
    return result


@router.post('/letsencrypt')
def tls_letsencrypt(
    data: TLSLetsEncryptInput,
    request: Request,
    actor=Depends(require('settings.update')),
    db=Depends(get_db, scope='function'),
):
    result = call('/tls/letsencrypt', 'POST', data.model_dump(exclude_none=True))
    audit(db, request, 'settings.tls.letsencrypt', 'system_tls', result.get('hostname'))
    return result


@router.post('/self-signed')
def tls_self_signed(
    data: TLSSelfSignedInput,
    request: Request,
    actor=Depends(require('settings.update')),
    db=Depends(get_db, scope='function'),
):
    result = call('/tls/self-signed', 'POST', data.model_dump(exclude_none=True))
    audit(db, request, 'settings.tls.self_signed', 'system_tls', result.get('hostname'))
    return result


@router.post('/sync')
def tls_sync(
    request: Request,
    actor=Depends(require('settings.update')),
    db=Depends(get_db, scope='function'),
):
    result = call('/tls/sync', 'POST', {})
    audit(db, request, 'settings.tls.synced', 'system_tls', result.get('hostname'))
    return result
