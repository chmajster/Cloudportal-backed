import copy
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.common import public
from app.models import (Audit, Credential, Job, ManagedResource, ManagedVM, Provider,
                        Setting, User, now)
from app.onboarding.classification import classification_preview
from app.onboarding.matching import find_matches
from app.onboarding.models import (DiscoveredResource, DiscoverySession, OnboardingConflict,
                                   OnboardingJobItem, ResourceExternalIdentity,
                                   ResourceSyncState)
from app.onboarding.providers.registry import onboarding_adapter, onboarding_capabilities
from app.projects.models import Project, ProjectMembership
from app.resource_scope.authorization import Scope
from app.resource_scope.database import bind_scope
from app.security.core import audit
from app.tenancy.models import Tenant
from app.vm_classification import vm_classification_for_tenant


DEFAULT_SETTINGS = {
    'discovery_concurrency': 5,
    'sync_interval_seconds': 300,
    'provider_retry_attempts': 3,
    'provider_retry_delay_seconds': 10,
}


def onboarding_settings(db):
    row = db.get(Setting, 'onboarding')
    value = dict(DEFAULT_SETTINGS)
    if row and isinstance(row.value, dict):
        value.update(row.value)
    value['discovery_concurrency'] = max(1, min(16, int(value.get('discovery_concurrency', 5))))
    value['sync_interval_seconds'] = max(60, min(86400, int(value.get('sync_interval_seconds', 300))))
    value['provider_retry_attempts'] = max(1, min(5, int(value.get('provider_retry_attempts', 3))))
    value['provider_retry_delay_seconds'] = max(1, min(60, int(value.get('provider_retry_delay_seconds', 10))))
    return value


def _iso(value):
    return value.isoformat() + 'Z' if value else None


def session_public(row):
    return {
        **public(
            row,
            'tenant_id project_id id provider_id job_id status scope_json filters_json '
            'discovered_count error_summary created_by created_at updated_at',
        ),
        'started_at': _iso(row.started_at),
        'completed_at': _iso(row.completed_at),
    }


def discovered_public(row, *, include_raw=False):
    normalized = copy.deepcopy(row.normalized_json or {})
    result = {
        'id': row.id,
        'session_id': row.session_id,
        'provider_id': row.provider_id,
        'cluster_id': row.cluster_id,
        'node_id': row.node_id,
        'resource_type': row.resource_type,
        'external_id': row.external_id,
        'provider_uuid': row.provider_uuid,
        'name': row.name,
        'hostname': row.hostname,
        'power_state': row.power_state,
        'is_template': row.is_template,
        'discovery_status': row.discovery_status,
        'normalized': normalized,
        'match_candidates': copy.deepcopy(row.match_candidates_json or []),
        'classification_trace': copy.deepcopy(row.rule_trace_json or []),
        'first_seen_at': _iso(row.first_seen_at),
        'last_seen_at': _iso(row.last_seen_at),
    }
    if include_raw:
        result['raw_metadata'] = copy.deepcopy(row.raw_metadata_json or {})
    return result


def identity_public(row, sync=None):
    return {
        **public(
            row,
            'tenant_id project_id id provider_id managed_vm_id managed_resource_id '
            'resource_type external_id cluster_id node_id provider_uuid management_mode '
            'management_source provisioning_source guest_credential_id awx_credential_id '
            'business_metadata_json provider_metadata_json onboarded_by created_at updated_at',
        ),
        'first_seen_at': _iso(row.first_seen_at),
        'last_seen_at': _iso(row.last_seen_at),
        'onboarded_at': _iso(row.onboarded_at),
        'retired_at': _iso(row.retired_at),
        'sync': sync_public(sync) if sync else None,
    }


def sync_public(row):
    return {
        'identity_id': row.identity_id,
        'status': row.status,
        'expected': copy.deepcopy(row.expected_json or {}),
        'actual': copy.deepcopy(row.actual_json or {}),
        'drift': copy.deepcopy(row.drift_json or {}),
        'last_sync_at': _iso(row.last_sync_at),
        'last_seen_at': _iso(row.last_seen_at),
        'next_sync_at': _iso(row.next_sync_at),
        'last_error': row.last_error,
    }


def job_item_public(row):
    return {
        **public(
            row,
            'id job_id discovered_resource_id external_identity_id provider_id external_id '
            'name status stage attempt error result_json created_at updated_at',
        )
    }


def _scope_of(request):
    scope = getattr(request.state, 'resource_scope', None)
    if scope is None:
        raise HTTPException(409, 'Project scope is required for onboarding')
    return scope


def _request_context(request):
    return {
        'request_id': str(getattr(request.state, 'request_id', '') or uuid.uuid4()),
        'ip': request.client.host if request.client else '',
        'source': str(getattr(request.state, 'source', '') or 'CloudPortal'),
    }


def provider_rows(db):
    rows = db.scalars(select(Provider).order_by(Provider.name)).all()
    return [{
        'id': row.id,
        'name': row.name,
        'type': row.type,
        'credentials_id': row.credentials_id,
        'capabilities': onboarding_capabilities(row.type),
    } for row in rows]


def provider_adapter(db, provider_id):
    provider = db.get(Provider, int(provider_id))
    if provider is None:
        raise HTTPException(404, 'Provider is not assigned to this project')
    credential = db.get(Credential, provider.credentials_id)
    if credential is None:
        raise HTTPException(404, 'Provider credential is not assigned to this project')
    if provider.type != credential.type:
        raise HTTPException(409, 'Provider and credential types do not match')
    return provider, credential, onboarding_adapter(provider, credential)


def create_discovery(db, request, actor, data):
    scope = _scope_of(request)
    provider_adapter(db, data.provider_id)
    session_row = DiscoverySession(
        provider_id=data.provider_id,
        status='QUEUED',
        scope_json=data.scope.model_dump(mode='json'),
        filters_json={},
        created_by=actor.user_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
    )
    db.add(session_row)
    db.flush()
    context = _request_context(request)
    job = Job(
        operation='onboarding.discovery',
        payload={'session_id': session_row.id, 'provider_id': data.provider_id},
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
    session_row.job_id = job.id
    audit(
        db, request, 'vm.discovery.requested', 'onboarding_discovery', session_row.id,
        metadata={'provider_id': data.provider_id, 'job_id': job.id},
    )
    return {'session': session_public(session_row), 'job': job_public(job)}


def job_public(job):
    payload = dict(job.payload or {})
    return {
        'id': job.id,
        'operation': job.operation,
        'status': job.status,
        'outcome': payload.get('onboarding_outcome'),
        'progress': payload.get('_progress'),
        'current_stage': payload.get('_current_stage'),
        'created_by': job.created_by,
        'created_at': _iso(job.created_at),
        'updated_at': _iso(job.updated_at),
        'error': job.error,
    }


def load_discovered(db, discovered_id):
    row = db.scalar(
        select(DiscoveredResource)
        .join(DiscoverySession, DiscoverySession.id == DiscoveredResource.session_id)
        .where(DiscoveredResource.id == discovered_id)
    )
    if row is None:
        raise HTTPException(404, 'Discovered resource not found in this project')
    return row


def list_discovered(db, session_id, *, status=None, query=None, resource_type=None, offset=0, limit=100):
    session_row = db.get(DiscoverySession, session_id)
    if session_row is None:
        raise HTTPException(404, 'Discovery session not found')
    statement = select(DiscoveredResource).where(DiscoveredResource.session_id == session_id)
    if status:
        statement = statement.where(DiscoveredResource.discovery_status == status.upper())
    if resource_type:
        statement = statement.where(DiscoveredResource.resource_type == resource_type.lower())
    if query:
        token = '%' + str(query).strip().lower() + '%'
        statement = statement.where(
            func.lower(DiscoveredResource.name).like(token)
            | func.lower(DiscoveredResource.hostname).like(token)
            | DiscoveredResource.external_id.like('%' + str(query).strip() + '%')
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(
        statement.order_by(DiscoveredResource.name, DiscoveredResource.external_id)
        .offset(offset).limit(limit)
    ).all()
    return {'items': [discovered_public(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}


def _classification(db, row, provider_type, manual):
    normalized = copy.deepcopy(row.normalized_json or {})
    normalized['provider_id'] = row.provider_id
    preview = classification_preview(db, normalized, provider_type=provider_type, manual=manual)
    row.rule_trace_json = preview['trace']
    return preview


def _validate_mapping(db, scope, mapping):
    errors = []
    tenant = db.get(Tenant, scope.tenant_id)
    project = db.get(Project, scope.project_id)
    if tenant is None:
        errors.append({'code': 'ORGANIZATION_NOT_FOUND', 'message': 'Organization no longer exists'})
    if project is None or project.tenant_id != scope.tenant_id:
        errors.append({'code': 'PROJECT_NOT_FOUND', 'message': 'Project no longer exists in the selected organization'})

    classification = vm_classification_for_tenant(db, scope.tenant_id)
    apmid = str(mapping.get('apmid') or '').strip().upper()
    environment = str(mapping.get('environment') or '').strip().lower()
    if not apmid:
        errors.append({'code': 'APMID_REQUIRED', 'message': 'APMID is required for onboarding'})
    elif apmid not in set(classification.get('apmids') or []):
        errors.append({'code': 'APMID_INVALID', 'message': 'APMID is not available in the selected organization'})
    if not environment:
        errors.append({'code': 'ENV_REQUIRED', 'message': 'Environment is required for onboarding'})
    elif not bool((classification.get('environments') or {}).get(environment)):
        errors.append({'code': 'ENV_INVALID', 'message': 'Environment is disabled or unknown'})

    owner_user_id = mapping.get('owner_user_id')
    if owner_user_id:
        member = db.scalar(select(ProjectMembership).where(
            ProjectMembership.project_id == scope.project_id,
            ProjectMembership.user_id == int(owner_user_id),
            ProjectMembership.status == 'active',
        ))
        if member is None or db.get(User, int(owner_user_id)) is None:
            errors.append({'code': 'OWNER_INVALID', 'message': 'Selected owner is not an active project member'})
    return errors


def _credential_check(db, credential_id, allowed_types, code):
    if not credential_id:
        return None, None
    credential = db.get(Credential, int(credential_id))
    if credential is None:
        return None, {'code': code, 'message': 'Credential is not assigned to this project'}
    if credential.type not in set(allowed_types):
        return None, {'code': code, 'message': 'Credential type is not valid for this integration'}
    if credential.expires_at is not None and credential.expires_at <= now():
        return None, {'code': code, 'message': 'Credential is expired'}
    return credential, None


def preflight(db, request, items):
    scope = _scope_of(request)
    permissions = set(getattr(request.state, 'permissions', set()))
    results = []
    if len(items) > 1 and 'vm.onboarding.bulk' not in permissions:
        raise HTTPException(403, 'Missing permission: vm.onboarding.bulk')

    adapters = {}
    for item in items:
        row = load_discovered(db, item.discovered_resource_id)
        provider = db.get(Provider, row.provider_id)
        errors = []
        warnings = []
        if provider is None:
            results.append({
                'discovered_resource_id': row.id, 'valid': False,
                'errors': [{'code': 'PROVIDER_NOT_FOUND', 'message': 'Provider is no longer available'}],
                'warnings': [], 'mapping': {}, 'sources': {}, 'capabilities': {},
            })
            continue

        required = {'vm.onboarding.create'}
        if item.mode == 'FULL_ADOPTION':
            required.add('vm.onboarding.full_adoption')
        if item.guest_credential_id:
            required.add('vm.onboarding.credentials.assign')
        if item.integrations.add_to_awx or item.awx_credential_id:
            required.add('vm.onboarding.awx.configure')
        missing_permissions = sorted(required - permissions)
        errors.extend({
            'code': 'PERMISSION_DENIED', 'message': 'Missing permission: ' + permission
        } for permission in missing_permissions)

        if item.mode == 'DISCOVER_ONLY':
            errors.append({
                'code': 'INVALID_MODE',
                'message': 'DISCOVER_ONLY does not create a resource; use the discovery session instead',
            })

        key = row.provider_id
        if key not in adapters:
            try:
                _provider, _credential, adapters[key] = provider_adapter(db, row.provider_id)
            except HTTPException as exc:
                errors.append({'code': 'PROVIDER_UNAVAILABLE', 'message': str(exc.detail)})
                adapters[key] = None
        adapter = adapters.get(key)

        live = None
        if adapter is not None:
            try:
                live = adapter.get_resource(resource_type=row.resource_type, external_id=row.external_id)
            except HTTPException as exc:
                errors.append({'code': 'PROVIDER_UNREACHABLE', 'message': str(exc.detail)})
            except Exception:
                errors.append({'code': 'PROVIDER_UNREACHABLE', 'message': 'Provider resource validation failed'})
        if adapter is not None and live is None and not any(error['code'] == 'PROVIDER_UNREACHABLE' for error in errors):
            errors.append({'code': 'RESOURCE_MISSING', 'message': 'VM no longer exists at the provider'})

        normalized = live.public() if live is not None else copy.deepcopy(row.normalized_json or {})
        normalized['provider_id'] = row.provider_id
        preview = _classification(
            db, row, provider.type,
            {key: value for key, value in item.mapping.model_dump().items() if value not in (None, '')},
        )
        mapping = preview['mapping']
        errors.extend(_validate_mapping(db, scope, mapping))

        guest_credential, credential_error = _credential_check(
            db, item.guest_credential_id, {'ssh', 'winrm'}, 'GUEST_CREDENTIAL_INVALID'
        )
        if credential_error:
            errors.append(credential_error)
        awx_credential, awx_error = _credential_check(
            db, item.awx_credential_id, {'awx'}, 'AWX_CREDENTIAL_INVALID'
        )
        if awx_error:
            errors.append(awx_error)
        if item.integrations.guest_discovery and guest_credential is None:
            errors.append({'code': 'GUEST_CREDENTIAL_REQUIRED', 'message': 'Guest discovery requires SSH or WinRM credential'})
        if item.integrations.install_guest_agent and guest_credential is None:
            errors.append({'code': 'GUEST_CREDENTIAL_REQUIRED', 'message': 'Guest Agent installation requires guest credential'})
        if item.integrations.add_to_awx and awx_credential is None:
            errors.append({'code': 'AWX_CREDENTIAL_REQUIRED', 'message': 'AWX onboarding requires AWX credential'})

        if row.discovery_status in {'POSSIBLE_MATCH', 'CONFLICT', 'DUPLICATE'}:
            errors.append({
                'code': 'UNRESOLVED_MATCH_CONFLICT',
                'message': 'Resolve the discovered-resource conflict before onboarding',
            })

        match = find_matches(db, normalized)
        if match['status'] in {'POSSIBLE_MATCH', 'CONFLICT', 'DUPLICATE'}:
            errors.append({
                'code': 'UNRESOLVED_MATCH_CONFLICT',
                'message': 'Matching engine found a possible existing resource',
            })

        capabilities = adapter.get_capabilities(live) if adapter is not None else {}
        if live is not None and adapter is not None:
            for message in adapter.validate_adoption(live, item.mode):
                (errors if message.get('severity') == 'error' else warnings).append(message)

        results.append({
            'discovered_resource_id': row.id,
            'name': row.name,
            'mode': item.mode,
            'valid': not errors,
            'errors': errors,
            'warnings': warnings,
            'mapping': mapping,
            'sources': preview['sources'],
            'classification_trace': preview['trace'],
            'capabilities': capabilities,
            'external_identity': {
                'provider_id': row.provider_id,
                'cluster_id': row.cluster_id,
                'resource_type': row.resource_type,
                'external_id': row.external_id,
                'provider_uuid': row.provider_uuid,
            },
            'would_change': {
                'create_inventory_record': match['status'] == 'NEW',
                'management_mode': item.mode,
                'management_source': 'onboarded',
                'provisioning_source': 'external',
                'guest_discovery': item.integrations.guest_discovery,
                'awx': item.integrations.add_to_awx,
            },
        })
    return {
        'valid': all(item['valid'] for item in results),
        'items': results,
        'summary': {
            'selected': len(results),
            'valid': sum(1 for item in results if item['valid']),
            'invalid': sum(1 for item in results if not item['valid']),
            'warnings': sum(len(item['warnings']) for item in results),
        },
    }


def dry_run(db, request, actor, items):
    result = preflight(db, request, items)
    audit(
        db, request, 'vm.onboarding.dry_run', 'onboarding', None,
        result='success' if result['valid'] else 'failure',
        metadata={'selected': len(items), 'valid': result['summary']['valid']},
    )
    return result


def queue_import(db, request, actor, items):
    check = preflight(db, request, items)
    if not check['valid']:
        raise HTTPException(422, {'code': 'PREFLIGHT_FAILED', 'report': check})
    scope = _scope_of(request)
    context = _request_context(request)
    payload_items = []
    for item, report in zip(items, check['items'], strict=True):
        payload_items.append({
            'discovered_resource_id': item.discovered_resource_id,
            'mode': item.mode,
            'mapping': report['mapping'],
            'mapping_sources': report['sources'],
            'guest_credential_id': item.guest_credential_id,
            'awx_credential_id': item.awx_credential_id,
            'integrations': item.integrations.model_dump(mode='json'),
        })
    job = Job(
        operation='onboarding.import',
        payload={'items': payload_items, 'selected': len(payload_items)},
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
    for config in payload_items:
        row = load_discovered(db, config['discovered_resource_id'])
        db.add(OnboardingJobItem(
            job_id=job.id,
            discovered_resource_id=row.id,
            provider_id=row.provider_id,
            external_id=row.external_id,
            name=row.name,
            status='QUEUED',
        ))
    audit(
        db, request, 'vm.onboarding.requested', 'onboarding_job', job.id,
        metadata={'selected': len(payload_items)},
    )
    return {'job': job_public(job), 'preflight': check}


def job_details(db, job_id):
    job = db.get(Job, job_id)
    if job is None or not str(job.operation).startswith('onboarding.'):
        raise HTTPException(404, 'Onboarding job not found')
    rows = db.scalars(
        select(OnboardingJobItem)
        .where(OnboardingJobItem.job_id == job_id)
        .order_by(OnboardingJobItem.created_at, OnboardingJobItem.name)
    ).all()
    summary = {
        'selected': len(rows),
        'completed': sum(1 for row in rows if row.status == 'COMPLETED'),
        'partial': sum(1 for row in rows if row.status == 'PARTIAL'),
        'failed': sum(1 for row in rows if row.status == 'FAILED'),
        'in_progress': sum(1 for row in rows if row.status in {'QUEUED', 'IN_PROGRESS', 'RETRYING'}),
    }
    return {'job': job_public(job), 'items': [job_item_public(row) for row in rows], 'summary': summary}


def retry_job(db, request, actor, job_id):
    original = db.get(Job, job_id)
    if original is None or original.operation != 'onboarding.import':
        raise HTTPException(404, 'Onboarding import job not found')
    rows = db.scalars(select(OnboardingJobItem).where(
        OnboardingJobItem.job_id == job_id,
        OnboardingJobItem.status.in_(['FAILED', 'PARTIAL']),
    )).all()
    if not rows:
        raise HTTPException(409, 'Onboarding job has no failed or partial resources to retry')
    allowed_ids = {row.discovered_resource_id for row in rows}
    configs = [
        dict(item) for item in (original.payload or {}).get('items') or []
        if item.get('discovered_resource_id') in allowed_ids
    ]
    scope = _scope_of(request)
    context = _request_context(request)
    job = Job(
        operation='onboarding.import',
        payload={'items': configs, 'selected': len(configs), 'retry_of': original.id},
        created_by=actor.user_id,
        token_id=actor.id,
        request_id=context['request_id'],
        ip=context['ip'],
        source=context['source'],
        retry_of=original.id,
        attempt=int(original.attempt or 1) + 1,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
    )
    db.add(job)
    db.flush()
    for config in configs:
        row = load_discovered(db, config['discovered_resource_id'])
        db.add(OnboardingJobItem(
            job_id=job.id,
            discovered_resource_id=row.id,
            provider_id=row.provider_id,
            external_id=row.external_id,
            name=row.name,
            status='QUEUED',
            attempt=job.attempt,
        ))
    audit(db, request, 'vm.onboarding.retry_requested', 'onboarding_job', job.id,
          metadata={'retry_of': original.id, 'selected': len(configs)})
    return job_details(db, job.id)


def cancel_job(db, request, job_id):
    job = db.get(Job, job_id)
    if job is None or not str(job.operation).startswith('onboarding.'):
        raise HTTPException(404, 'Onboarding job not found')
    if job.status not in {'queued', 'running', 'cancelling'}:
        raise HTTPException(409, 'Onboarding job is already terminal')
    job.cancel_requested = True
    if job.status == 'running':
        job.status = 'cancelling'
    audit(db, request, 'vm.onboarding.cancel_requested', 'onboarding_job', job.id)
    return job_public(job)


def resource_snapshot(resource):
    return {
        'cpu': resource.get('cpu'),
        'memory_mb': resource.get('memory_mb'),
        'disks': resource.get('disks') or [],
        'networks': resource.get('networks') or [],
        'tags': resource.get('tags') or [],
    }


def drift(expected, actual):
    changes = {}
    for key in sorted(set(expected or {}) | set(actual or {})):
        before = (expected or {}).get(key)
        after = (actual or {}).get(key)
        if before != after:
            changes[key] = {'expected': before, 'actual': after}
    return changes


def dashboard(db):
    latest = db.scalar(select(DiscoverySession).order_by(DiscoverySession.created_at.desc()).limit(1))
    counts = {}
    if latest:
        rows = db.execute(
            select(DiscoveredResource.discovery_status, func.count())
            .where(DiscoveredResource.session_id == latest.id)
            .group_by(DiscoveredResource.discovery_status)
        ).all()
        counts = {status: count for status, count in rows}
    sync_rows = db.execute(
        select(ResourceSyncState.status, func.count()).group_by(ResourceSyncState.status)
    ).all()
    sync_counts = {status: count for status, count in sync_rows}
    jobs = db.scalars(
        select(Job).where(Job.operation.like('onboarding.%'))
        .order_by(Job.created_at.desc()).limit(10)
    ).all()
    return {
        'last_discovery': session_public(latest) if latest else None,
        'statistics': {
            'discovered': sum(counts.values()),
            'managed': counts.get('MANAGED', 0),
            'inventory_only': counts.get('INVENTORY_ONLY', 0),
            'available': counts.get('NEW', 0),
            'conflicts': counts.get('CONFLICT', 0) + counts.get('POSSIBLE_MATCH', 0) + counts.get('DUPLICATE', 0),
            'missing': sync_counts.get('MISSING', 0),
            'unreachable': sync_counts.get('UNREACHABLE', 0),
            'drifted': sync_counts.get('DRIFTED', 0),
        },
        'recent_jobs': [job_public(job) for job in jobs],
    }


def bind_job_scope(db, job):
    bind_scope(db, Scope(job.tenant_id, job.project_id))


def worker_audit_event(db, job, action, resource, resource_id, payload=None, *, result='success'):
    from app.events.service import publish_event
    safe_payload = copy.deepcopy(payload or {})
    db.add(Audit(
        user_id=job.created_by,
        token_id=job.token_id,
        ip=job.ip,
        source=job.source,
        action=action,
        resource=resource,
        resource_id=str(resource_id) if resource_id is not None else None,
        result=result,
        request_id=job.request_id,
    ))
    publish_event(
        db,
        action,
        {
            'context': {
                'user_id': job.created_by,
                'request_id': job.request_id,
                'tenant_id': job.tenant_id,
                'project_id': job.project_id,
                'source': job.source,
            },
            **safe_payload,
        },
        subject_type=resource,
        subject_id=str(resource_id) if resource_id is not None else None,
        request_id=job.request_id,
    )


def identity_for_resource(db, resource_id):
    return db.scalar(select(ResourceExternalIdentity).where(
        (ResourceExternalIdentity.managed_vm_id == resource_id)
        | (ResourceExternalIdentity.managed_resource_id == resource_id),
        ResourceExternalIdentity.retired_at.is_(None),
    ))


def refresh_identity(db, identity):
    provider, _credential, adapter = provider_adapter(db, identity.provider_id)
    try:
        live = adapter.refresh_resource(identity)
    except HTTPException as exc:
        state = db.get(ResourceSyncState, identity.id) or ResourceSyncState(identity_id=identity.id)
        state.status = 'UNREACHABLE'
        state.last_sync_at = now()
        state.last_error = str(exc.detail)[:1000]
        db.add(state)
        return identity, state, None
    if live is None:
        state = db.get(ResourceSyncState, identity.id) or ResourceSyncState(identity_id=identity.id)
        state.status = 'MISSING'
        state.last_sync_at = now()
        state.last_error = None
        db.add(state)
        if identity.managed_vm_id:
            vm = db.get(ManagedVM, identity.managed_vm_id)
            if vm:
                vm.lifecycle_status = 'missing'
        elif identity.managed_resource_id:
            resource = db.get(ManagedResource, identity.managed_resource_id)
            if resource:
                resource.lifecycle_status = 'missing'
        return identity, state, None

    normalized = live.public()
    actual = resource_snapshot(normalized)
    state = db.get(ResourceSyncState, identity.id)
    if state is None:
        state = ResourceSyncState(identity_id=identity.id, expected_json=actual)
        db.add(state)
    differences = drift(state.expected_json or actual, actual)
    state.actual_json = actual
    state.drift_json = differences
    state.status = 'DRIFTED' if differences else 'SYNCED'
    state.last_sync_at = now()
    state.last_seen_at = now()
    state.next_sync_at = now() + timedelta(seconds=onboarding_settings(db)['sync_interval_seconds'])
    state.last_error = None
    identity.last_seen_at = now()
    identity.node_id = str((normalized.get('location') or {}).get('node') or identity.node_id)
    identity.provider_metadata_json = normalized
    if identity.managed_vm_id:
        vm = db.get(ManagedVM, identity.managed_vm_id)
        if vm:
            vm.node = identity.node_id
            vm.name = normalized.get('name') or vm.name
            vm.lifecycle_status = 'active'
    elif identity.managed_resource_id:
        resource = db.get(ManagedResource, identity.managed_resource_id)
        if resource:
            resource.name = normalized.get('name') or resource.name
            resource.primary_ip = (normalized.get('addresses') or [None])[0]
            resource.lifecycle_status = 'active'
    return identity, state, normalized
