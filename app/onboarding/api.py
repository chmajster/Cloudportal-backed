import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.common import idempotent
from app.api.provider_diagnostics import diagnose_provider
from app.database import get_db
from app.models import Credential, Job, ManagedResource, ManagedVM, Provider, now
from app.onboarding.classification import validate_rule_document
from app.onboarding.models import (DiscoveredResource, DiscoverySession, OnboardingConflict,
                                   OnboardingRule, OnboardingSchedule,
                                   ResourceExternalIdentity, ResourceSyncState)
from app.onboarding.providers.registry import onboarding_adapter
from app.onboarding.schemas import (ConflictResolveInput, DiscoveryInput, ImportInput,
                                    PreflightInput, RelinkInput, RuleInput, RulePatch,
                                    ScheduleInput, SchedulePatch)
from app.onboarding.service import (cancel_job, create_discovery, dashboard, discovered_public,
                                    dry_run, identity_for_resource, identity_public, job_details,
                                    job_public, list_discovered, preflight, provider_rows,
                                    queue_import, refresh_identity, retry_job, session_public,
                                    sync_public)
from app.resource_scope.http import require
from app.security.core import audit


router = APIRouter(prefix='/onboarding', tags=['onboarding'])


@router.get('/providers')
def providers(actor=Depends(require('vm.discovery.read')), db=Depends(get_db, scope='function')):
    return {'items': provider_rows(db)}


@router.post('/providers/{provider_id}/connection-test')
def provider_connection_test(
    provider_id: int,
    request: Request,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(404, 'Provider is not assigned to this project')
    credential = db.get(Credential, provider.credentials_id)
    if credential is None:
        raise HTTPException(404, 'Provider credential is not assigned to this project')
    result = diagnose_provider(provider, credential, deep=True)
    try:
        adapter = onboarding_adapter(provider, credential)
        result['onboarding_permission_checks'] = adapter.permission_diagnostics()
    except HTTPException as exc:
        result['onboarding_permission_checks'] = [{
            'id': 'onboarding_adapter',
            'label': 'Brownfield discovery',
            'status': 'fail',
            'message': str(exc.detail),
        }]
    audit(db, request, 'vm.discovery.connection_test', 'providers', str(provider_id),
          result='success' if result.get('status') in {'ok', 'degraded'} else 'failure')
    return result


@router.get('/dashboard')
def onboarding_dashboard(
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    return dashboard(db)


@router.post('/discovery', status_code=202)
def start_discovery(
    data: DiscoveryInput,
    request: Request,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    return idempotent(
        db, request, actor, data.model_dump(mode='json'),
        lambda: create_discovery(db, request, actor, data),
    )


@router.get('/discovery/{session_id}')
def get_discovery(
    session_id: str,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(DiscoverySession, session_id)
    if row is None:
        raise HTTPException(404, 'Discovery session not found')
    result = session_public(row)
    if row.job_id:
        job = db.get(Job, row.job_id)
        result['job'] = job_public(job) if job else None
    return result


@router.get('/discovery/{session_id}/resources')
def discovery_resources(
    session_id: str,
    status: str | None = None,
    q: str | None = Query(default=None, max_length=255),
    resource_type: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    return list_discovered(
        db, session_id, status=status, query=q, resource_type=resource_type,
        offset=offset, limit=limit,
    )


@router.get('/discovered/{resource_id}')
def discovered_details(
    resource_id: str,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(
        select(DiscoveredResource)
        .join(DiscoverySession, DiscoverySession.id == DiscoveredResource.session_id)
        .where(DiscoveredResource.id == resource_id)
    )
    if row is None:
        raise HTTPException(404, 'Discovered resource not found')
    return discovered_public(row, include_raw=True)


@router.post('/preflight')
def onboarding_preflight(
    data: PreflightInput,
    request: Request,
    actor=Depends(require('vm.onboarding.create')),
    db=Depends(get_db, scope='function'),
):
    return preflight(db, request, data.items)


@router.post('/dry-run')
def onboarding_dry_run(
    data: PreflightInput,
    request: Request,
    actor=Depends(require('vm.onboarding.create')),
    db=Depends(get_db, scope='function'),
):
    return dry_run(db, request, actor, data.items)


@router.post('/import', status_code=202)
def onboarding_import(
    data: ImportInput,
    request: Request,
    actor=Depends(require('vm.onboarding.create')),
    db=Depends(get_db, scope='function'),
):
    return idempotent(
        db, request, actor, data.model_dump(mode='json'),
        lambda: queue_import(db, request, actor, data.items),
        required=True,
    )


@router.get('/jobs/{job_id}')
def onboarding_job(
    job_id: str,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    return job_details(db, job_id)


@router.post('/jobs/{job_id}/retry', status_code=202)
def onboarding_job_retry(
    job_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    return retry_job(db, request, actor, job_id)


@router.post('/jobs/{job_id}/cancel')
def onboarding_job_cancel(
    job_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    return cancel_job(db, request, job_id)


@router.get('/jobs/{job_id}/report')
def onboarding_job_report(
    job_id: str,
    format: str = Query(default='json', pattern='^(json|csv)$'),
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    report = job_details(db, job_id)
    if format == 'json':
        return report
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=['external_id', 'name', 'status', 'stage', 'attempt', 'error', 'resource_id'],
    )
    writer.writeheader()
    for item in report['items']:
        writer.writerow({
            'external_id': item['external_id'],
            'name': item['name'],
            'status': item['status'],
            'stage': item['stage'],
            'attempt': item['attempt'],
            'error': item['error'] or '',
            'resource_id': (item.get('result_json') or {}).get('resource_id') or '',
        })
    return PlainTextResponse(
        stream.getvalue(),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="onboarding-{job_id}.csv"'},
    )


@router.post('/resources/{resource_id}/refresh', status_code=202)
def refresh_onboarded_resource(
    resource_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    identity = identity_for_resource(db, resource_id)
    if identity is None:
        raise HTTPException(404, 'Onboarded resource identity not found')
    scope = request.state.resource_scope
    context = {
        'request_id': str(getattr(request.state, 'request_id', '') or ''),
        'ip': request.client.host if request.client else '',
        'source': str(getattr(request.state, 'source', '') or 'CloudPortal'),
    }
    job = Job(
        operation='onboarding.sync',
        payload={'identity_id': identity.id, 'identity_ids': [identity.id]},
        created_by=actor.user_id,
        token_id=actor.id,
        request_id=context['request_id'],
        ip=context['ip'],
        source=context['source'],
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
    )
    db.add(job)
    db.flush()
    audit(db, request, 'vm.sync.requested', 'resource', resource_id,
          metadata={'identity_id': identity.id, 'job_id': job.id})
    return {'job': job_public(job)}


@router.get('/resources/{resource_id}/sync')
def onboarded_resource_sync(
    resource_id: str,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    identity = identity_for_resource(db, resource_id)
    if identity is None:
        raise HTTPException(404, 'Onboarded resource identity not found')
    sync = db.get(ResourceSyncState, identity.id)
    return identity_public(identity, sync)


@router.post('/resources/{resource_id}/relink')
def relink_onboarded_resource(
    resource_id: str,
    data: RelinkInput,
    request: Request,
    actor=Depends(require('vm.onboarding.conflicts.resolve')),
    db=Depends(get_db, scope='function'),
):
    identity = identity_for_resource(db, resource_id)
    if identity is None:
        raise HTTPException(404, 'Onboarded resource identity not found')
    discovered = db.scalar(
        select(DiscoveredResource)
        .join(DiscoverySession, DiscoverySession.id == DiscoveredResource.session_id)
        .where(DiscoveredResource.id == data.discovered_resource_id)
    )
    if discovered is None:
        raise HTTPException(404, 'Discovered resource not found')
    if identity.provider_id != discovered.provider_id:
        raise HTTPException(409, 'Provider mismatch prevents relinking')
    identity.cluster_id = discovered.cluster_id
    identity.node_id = discovered.node_id
    identity.resource_type = discovered.resource_type
    identity.external_id = discovered.external_id
    identity.provider_uuid = discovered.provider_uuid
    identity.last_seen_at = now()
    audit(db, request, 'vm.onboarding.resource_relinked', 'resource', resource_id,
          metadata={'external_identity_id': identity.id, 'discovered_resource_id': discovered.id})
    return identity_public(identity, db.get(ResourceSyncState, identity.id))


@router.delete('/resources/{resource_id}')
def remove_onboarded_resource(
    resource_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.remove')),
    db=Depends(get_db, scope='function'),
):
    identity = identity_for_resource(db, resource_id)
    if identity is None:
        raise HTTPException(404, 'Onboarded resource identity not found')
    identity.retired_at = now()
    identity.management_mode = 'RETIRED'
    if identity.managed_vm_id:
        row = db.get(ManagedVM, identity.managed_vm_id)
    else:
        row = db.get(ManagedResource, identity.managed_resource_id)
    if row is not None:
        row.lifecycle_status = 'destroyed'
        row.destroyed_at = now()
    audit(db, request, 'vm.onboarding.removed', 'resource', resource_id,
          metadata={'external_identity_id': identity.id})
    return {'removed': True, 'resource_id': resource_id, 'provider_resource_untouched': True}


def _rule_public(row):
    return {
        'id': row.id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'name': row.name,
        'priority': row.priority,
        'enabled': row.enabled,
        'stop_processing': row.stop_processing,
        'conditions': row.conditions_json or {},
        'effects': row.effects_json or {},
        'created_by': row.created_by,
        'created_at': row.created_at.isoformat() + 'Z',
        'updated_at': row.updated_at.isoformat() + 'Z',
    }


@router.get('/rules')
def rules(
    actor=Depends(require('vm.onboarding.rules.read')),
    db=Depends(get_db, scope='function'),
):
    rows = db.scalars(select(OnboardingRule).order_by(
        OnboardingRule.priority, OnboardingRule.name
    )).all()
    return {'items': [_rule_public(row) for row in rows]}


@router.post('/rules', status_code=201)
def create_rule(
    data: RuleInput,
    request: Request,
    actor=Depends(require('vm.onboarding.rules.manage')),
    db=Depends(get_db, scope='function'),
):
    try:
        validate_rule_document(data.conditions, data.effects)
    except (ValueError, Exception) as exc:
        raise HTTPException(422, str(exc)) from None
    scope = request.state.resource_scope
    row = OnboardingRule(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        name=data.name,
        priority=data.priority,
        enabled=data.enabled,
        stop_processing=data.stop_processing,
        conditions_json=data.conditions,
        effects_json=data.effects,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit(db, request, 'vm.onboarding.rule_created', 'onboarding_rule', row.id)
    return _rule_public(row)


@router.patch('/rules/{rule_id}')
def update_rule(
    rule_id: str,
    data: RulePatch,
    request: Request,
    actor=Depends(require('vm.onboarding.rules.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(OnboardingRule, rule_id)
    if row is None:
        raise HTTPException(404, 'Onboarding rule not found')
    values = data.model_dump(exclude_unset=True)
    conditions = values.get('conditions', row.conditions_json or {})
    effects = values.get('effects', row.effects_json or {})
    try:
        validate_rule_document(conditions, effects)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None
    for key in ('name', 'priority', 'enabled', 'stop_processing'):
        if key in values:
            setattr(row, key, values[key])
    if 'conditions' in values:
        row.conditions_json = values['conditions']
    if 'effects' in values:
        row.effects_json = values['effects']
    audit(db, request, 'vm.onboarding.rule_updated', 'onboarding_rule', row.id)
    return _rule_public(row)


@router.delete('/rules/{rule_id}', status_code=204)
def delete_rule(
    rule_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.rules.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(OnboardingRule, rule_id)
    if row is None:
        raise HTTPException(404, 'Onboarding rule not found')
    db.delete(row)
    audit(db, request, 'vm.onboarding.rule_deleted', 'onboarding_rule', rule_id)


def _conflict_public(row):
    return {
        'id': row.id,
        'discovered_resource_id': row.discovered_resource_id,
        'provider_id': row.provider_id,
        'conflict_type': row.conflict_type,
        'status': row.status,
        'candidates': row.candidates_json or [],
        'details': row.details_json or {},
        'resolution': row.resolution,
        'resolved_by': row.resolved_by,
        'resolved_at': row.resolved_at.isoformat() + 'Z' if row.resolved_at else None,
        'created_at': row.created_at.isoformat() + 'Z',
    }


@router.get('/conflicts')
def conflicts(
    status: str | None = None,
    actor=Depends(require('vm.discovery.read')),
    db=Depends(get_db, scope='function'),
):
    statement = select(OnboardingConflict)
    if status:
        statement = statement.where(OnboardingConflict.status == status.upper())
    rows = db.scalars(statement.order_by(OnboardingConflict.created_at.desc()).limit(500)).all()
    return {'items': [_conflict_public(row) for row in rows]}


@router.post('/conflicts/{conflict_id}/resolve')
def resolve_conflict(
    conflict_id: str,
    data: ConflictResolveInput,
    request: Request,
    actor=Depends(require('vm.onboarding.conflicts.resolve')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(OnboardingConflict, conflict_id)
    if row is None:
        raise HTTPException(404, 'Onboarding conflict not found')
    if row.status != 'OPEN':
        raise HTTPException(409, 'Conflict is already resolved')
    discovered = db.get(DiscoveredResource, row.discovered_resource_id) if row.discovered_resource_id else None
    if discovered is None:
        raise HTTPException(409, 'Discovered resource no longer exists')

    if data.action == 'LINK_EXISTING':
        managed = db.get(ManagedVM, data.managed_vm_id)
        if managed is None:
            raise HTTPException(404, 'Managed VM target not found')
        existing = db.scalar(select(ResourceExternalIdentity).where(
            ResourceExternalIdentity.provider_id == discovered.provider_id,
            ResourceExternalIdentity.cluster_id == discovered.cluster_id,
            ResourceExternalIdentity.resource_type == discovered.resource_type,
            ResourceExternalIdentity.external_id == discovered.external_id,
            ResourceExternalIdentity.retired_at.is_(None),
        ))
        if existing and existing.managed_vm_id != managed.id:
            raise HTTPException(409, 'External identity is already linked to another resource')
        if existing is None:
            existing = ResourceExternalIdentity(
                tenant_id=request.state.resource_scope.tenant_id,
                project_id=request.state.resource_scope.project_id,
                provider_id=discovered.provider_id,
                managed_vm_id=managed.id,
                resource_type=discovered.resource_type,
                external_id=discovered.external_id,
                cluster_id=discovered.cluster_id,
                node_id=discovered.node_id,
                provider_uuid=discovered.provider_uuid,
                management_mode='MANAGED',
                management_source='onboarded',
                provisioning_source='unknown',
                business_metadata_json={},
                provider_metadata_json=discovered.normalized_json or {},
                first_seen_at=discovered.first_seen_at,
                last_seen_at=discovered.last_seen_at,
                onboarded_at=now(),
                onboarded_by=actor.user_id,
            )
            db.add(existing)
        discovered.discovery_status = 'MANAGED'
    elif data.action in {'IMPORT_NEW', 'MARK_EXTERNAL', 'REMOVE_STALE'}:
        if data.action == 'REMOVE_STALE':
            candidate = next(
                (item for item in (row.candidates_json or []) if item.get('kind') == 'managed_vm'),
                None,
            )
            if candidate:
                stale = db.get(ManagedVM, candidate.get('id'))
                if stale and stale.lifecycle_status in {'missing', 'destroyed'}:
                    stale.lifecycle_status = 'destroyed'
                    stale.destroyed_at = stale.destroyed_at or now()
                elif stale:
                    raise HTTPException(409, 'Only a missing or destroyed stale record can be removed')
        discovered.discovery_status = 'NEW'
    elif data.action == 'IGNORE':
        discovered.discovery_status = 'UNSUPPORTED'

    row.status = 'RESOLVED'
    row.resolution = data.action
    row.resolved_by = actor.user_id
    row.resolved_at = now()
    audit(
        db, request, 'vm.onboarding.conflict_resolved', 'onboarding_conflict', row.id,
        metadata={'resolution': data.action, 'managed_vm_id': data.managed_vm_id},
    )
    return _conflict_public(row)


def _schedule_public(row):
    return {
        'id': row.id,
        'name': row.name,
        'provider_id': row.provider_id,
        'discovery_scope': row.discovery_scope_json or {},
        'interval_seconds': row.interval_seconds,
        'next_run_at': row.next_run_at.isoformat() + 'Z',
        'last_run_at': row.last_run_at.isoformat() + 'Z' if row.last_run_at else None,
        'auto_classification': row.auto_classification,
        'automatic_inventory_import': row.automatic_inventory_import,
        'is_active': row.is_active,
        'last_error': row.last_error,
        'created_by': row.created_by,
    }


@router.get('/schedules')
def schedules(
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    rows = db.scalars(select(OnboardingSchedule).order_by(OnboardingSchedule.name)).all()
    return {'items': [_schedule_public(row) for row in rows]}


@router.post('/schedules', status_code=201)
def create_schedule(
    data: ScheduleInput,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    provider = db.get(Provider, data.provider_id)
    if provider is None:
        raise HTTPException(404, 'Provider is not assigned to this project')
    scope = request.state.resource_scope
    row = OnboardingSchedule(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        name=data.name,
        provider_id=data.provider_id,
        discovery_scope_json=data.discovery_scope.model_dump(mode='json'),
        filters_json={},
        interval_seconds=data.interval_seconds,
        next_run_at=data.next_run_at or (now() + timedelta(seconds=data.interval_seconds)),
        auto_classification=data.auto_classification,
        automatic_inventory_import=data.automatic_inventory_import,
        is_active=data.is_active,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit(db, request, 'vm.discovery.schedule_created', 'onboarding_schedule', row.id)
    return _schedule_public(row)


@router.patch('/schedules/{schedule_id}')
def update_schedule(
    schedule_id: str,
    data: SchedulePatch,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(OnboardingSchedule, schedule_id)
    if row is None:
        raise HTTPException(404, 'Onboarding schedule not found')
    values = data.model_dump(exclude_unset=True)
    for key in ('name', 'interval_seconds', 'next_run_at', 'auto_classification', 'automatic_inventory_import', 'is_active'):
        if key in values:
            setattr(row, key, values[key])
    if 'discovery_scope' in values:
        value = values['discovery_scope']
        row.discovery_scope_json = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    audit(db, request, 'vm.discovery.schedule_updated', 'onboarding_schedule', row.id)
    return _schedule_public(row)


@router.delete('/schedules/{schedule_id}', status_code=204)
def delete_schedule(
    schedule_id: str,
    request: Request,
    actor=Depends(require('vm.onboarding.manage')),
    db=Depends(get_db, scope='function'),
):
    row = db.get(OnboardingSchedule, schedule_id)
    if row is None:
        raise HTTPException(404, 'Onboarding schedule not found')
    db.delete(row)
    audit(db, request, 'vm.discovery.schedule_deleted', 'onboarding_schedule', schedule_id)
