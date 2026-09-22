"""Project-scoped quota API; tenant ceilings remain a tenant-level authority."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.database import get_db
from app.quotas.models import QuotaReservation
from app.quotas import service
from app.resource_scope.http import require
from app.security.core import audit

router = APIRouter(tags=['quotas'])


class QuotaLimitInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(ge=0)


class ReconcileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['commit', 'release']


def reservation_public(row):
    return {
        'id': row.id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'request_key': row.request_key,
        'subject_type': row.subject_type,
        'subject_id': row.subject_id,
        'operation': row.operation,
        'deltas': row.deltas or {},
        'status': row.status,
        'reconciliation_required': row.reconciliation_required,
        'created_at': row.created_at.isoformat() + 'Z' if row.created_at else None,
        'committed_at': row.committed_at.isoformat() + 'Z' if row.committed_at else None,
        'released_at': row.released_at.isoformat() + 'Z' if row.released_at else None,
        'reconciled_at': row.reconciled_at.isoformat() + 'Z' if row.reconciled_at else None,
    }


@router.get('/quotas')
def quotas(request: Request, actor=Depends(require('quotas.read')), db=Depends(get_db, scope='function')):
    return service.quota_snapshot(db, request.state.resource_scope)


@router.put('/quotas/project/{dimension}')
def project_limit(dimension: str, data: QuotaLimitInput, request: Request,
                  actor=Depends(require('quotas.manage')), db=Depends(get_db, scope='function')):
    row = service.set_project_limit(db, request.state.resource_scope, dimension, data.limit)
    audit(db, request, 'quota.project_limit.updated', 'projects', row.project_id)
    return {'tenant_id': row.tenant_id, 'project_id': row.project_id,
            'dimension': row.dimension, 'limit': row.limit_value}


@router.put('/quotas/tenant/{dimension}')
def tenant_limit(dimension: str, data: QuotaLimitInput, request: Request,
                 actor=Depends(require('quotas.tenant.manage')), db=Depends(get_db, scope='function')):
    row = service.set_tenant_limit(db, request.state.resource_scope, dimension, data.limit)
    audit(db, request, 'quota.tenant_limit.updated', 'tenants', row.tenant_id)
    return {'tenant_id': row.tenant_id, 'dimension': row.dimension, 'limit': row.limit_value}


@router.get('/quotas/reservations')
def reservations(request: Request, status: Literal['reserved', 'uncertain', 'committed', 'released'] | None = None,
                 actor=Depends(require('quotas.read')), db=Depends(get_db, scope='function')):
    query = select(QuotaReservation).order_by(QuotaReservation.created_at.desc()).limit(200)
    if status:
        query = query.where(QuotaReservation.status == status)
    return {'items': [reservation_public(row) for row in db.scalars(query).all()]}


@router.post('/quotas/reservations/{reservation_id}/reconcile')
def reconcile(reservation_id: str, data: ReconcileInput, request: Request,
              actor=Depends(require('quotas.manage')), db=Depends(get_db, scope='function')):
    row = service.reconcile_reservation(db, reservation_id, data.outcome)
    audit(db, request, 'quota.reservation.reconciled', 'quota_reservations', row.id)
    return reservation_public(row)
