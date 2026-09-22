"""Quota limits, usage, atomic reservations and provider-confirmed accounting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Deployment, Job, ManagedResource, ManagedVM, now
from app.projects.models import Project
from app.tenancy.models import Tenant
from app.resource_scope.authorization import Scope
from app.tenancy.authorization import fail
from app.quotas.models import (
    ProjectQuotaLimit,
    ProjectQuotaUsage,
    QuotaAllocation,
    QuotaLedgerEntry,
    QuotaReservation,
    TenantQuotaLimit,
    TenantQuotaUsage,
)

DIMENSIONS = ('vm_count', 'vcpu', 'memory_mb', 'disk_gib')
ACTIVE_RESERVATION_STATES = ('reserved', 'uncertain')
MUTATING_TERRAFORM = {'terraform.apply', 'terraform.import', 'terraform.destroy'}


@dataclass(frozen=True, slots=True)
class QuotaDelta:
    scope: Scope
    subject_type: str
    subject_id: str
    operation: str
    deltas: dict[str, int]


def _dimension(value: str) -> str:
    value = str(value)
    if value not in DIMENSIONS:
        fail(422, 'INVALID_QUOTA_DIMENSION', f'Unsupported quota dimension: {value}')
    return value


def _values(values: Mapping | None, *, signed: bool = True) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, raw in dict(values or {}).items():
        key = _dimension(key)
        if isinstance(raw, bool):
            fail(422, 'INVALID_QUOTA_VALUE', 'Quota values must be integers')
        try:
            value = int(raw)
        except (TypeError, ValueError):
            fail(422, 'INVALID_QUOTA_VALUE', 'Quota values must be integers')
        if not signed and value < 0:
            fail(422, 'INVALID_QUOTA_VALUE', 'Quota values cannot be negative')
        if value:
            result[key] = value
    return result


def _positive(deltas: Mapping[str, int]) -> dict[str, int]:
    return {key: max(0, int(value)) for key, value in deltas.items() if int(value) > 0}


def _nonnegative_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _scope(row) -> Scope:
    return Scope(str(row.tenant_id), str(row.project_id))


def _lock_scope(db, scope: Scope):
    # Tenant first, project second. The same lock order is used for limit
    # changes, reservations, commits and reconciliation.
    tenant = db.scalar(select(Tenant).where(Tenant.id == scope.tenant_id).with_for_update())
    project = db.scalar(select(Project).where(
        Project.tenant_id == scope.tenant_id, Project.id == scope.project_id
    ).with_for_update())
    if tenant is None or project is None:
        fail(404, 'PROJECT_NOT_FOUND', 'Quota scope no longer exists')
    return tenant, project


def _project_limit(db, scope: Scope, dimension: str, *, lock: bool = False):
    query = select(ProjectQuotaLimit).where(
        ProjectQuotaLimit.tenant_id == scope.tenant_id,
        ProjectQuotaLimit.project_id == scope.project_id,
        ProjectQuotaLimit.dimension == dimension,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _tenant_limit(db, scope: Scope, dimension: str, *, lock: bool = False):
    query = select(TenantQuotaLimit).where(
        TenantQuotaLimit.tenant_id == scope.tenant_id,
        TenantQuotaLimit.dimension == dimension,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _usage_query(model, scope: Scope, dimension: str):
    if model is TenantQuotaUsage:
        return select(model).where(model.tenant_id == scope.tenant_id, model.dimension == dimension)
    return select(model).where(
        model.tenant_id == scope.tenant_id,
        model.project_id == scope.project_id,
        model.dimension == dimension,
    )


def _get_or_create_usage(db, model, scope: Scope, dimension: str):
    query = _usage_query(model, scope, dimension)
    row = db.scalar(query.with_for_update())
    if row is not None:
        return row
    values = {'tenant_id': scope.tenant_id, 'dimension': dimension, 'used': 0, 'reserved': 0}
    if model is ProjectQuotaUsage:
        values['project_id'] = scope.project_id
    candidate = model(**values)
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
    except IntegrityError:
        pass
    row = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if row is None:
        fail(409, 'QUOTA_ACCOUNTING_CONFLICT', 'Quota usage row could not be locked')
    return row


def _usage_read(db, model, scope: Scope, dimension: str):
    return db.scalar(_usage_query(model, scope, dimension))


def _ledger(db, reservation: QuotaReservation, event: str, deltas: Mapping[str, int]):
    db.add(QuotaLedgerEntry(
        tenant_id=reservation.tenant_id,
        project_id=reservation.project_id,
        reservation_id=reservation.id,
        event=event,
        subject_type=reservation.subject_type,
        subject_id=reservation.subject_id,
        deltas=dict(deltas),
    ))


def _allocation(db, scope: Scope, subject_type: str, subject_id: str, *, lock: bool = False):
    query = select(QuotaAllocation).where(
        QuotaAllocation.tenant_id == scope.tenant_id,
        QuotaAllocation.project_id == scope.project_id,
        QuotaAllocation.subject_type == subject_type,
        QuotaAllocation.subject_id == str(subject_id),
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def allocation_dimensions(db, scope: Scope, subject_type: str, subject_id: str) -> dict[str, int]:
    row = _allocation(db, scope, subject_type, subject_id)
    return _values(row.dimensions if row else {}, signed=False)


def quota_snapshot(db, scope: Scope):
    items = []
    for dimension in DIMENSIONS:
        tenant_limit = _tenant_limit(db, scope, dimension)
        project_limit = _project_limit(db, scope, dimension)
        tenant_usage = _usage_read(db, TenantQuotaUsage, scope, dimension)
        project_usage = _usage_read(db, ProjectQuotaUsage, scope, dimension)
        limits = [row.limit_value for row in (tenant_limit, project_limit) if row is not None]
        items.append({
            'dimension': dimension,
            'tenant_limit': tenant_limit.limit_value if tenant_limit else None,
            'project_limit': project_limit.limit_value if project_limit else None,
            'effective_limit': min(limits) if limits else None,
            'tenant_used': int(tenant_usage.used if tenant_usage else 0),
            'tenant_reserved': int(tenant_usage.reserved if tenant_usage else 0),
            'project_used': int(project_usage.used if project_usage else 0),
            'project_reserved': int(project_usage.reserved if project_usage else 0),
        })
    return {'tenant_id': scope.tenant_id, 'project_id': scope.project_id, 'items': items}


def set_project_limit(db, scope: Scope, dimension: str, limit_value: int):
    dimension = _dimension(dimension)
    _lock_scope(db, scope)
    _ensure_limit_accountable(db, scope, dimension, tenant_wide=False)
    if isinstance(limit_value, bool) or int(limit_value) < 0:
        fail(422, 'INVALID_QUOTA_VALUE', 'Quota limit must be a non-negative integer')
    row = _project_limit(db, scope, dimension, lock=True)
    if row is None:
        candidate = ProjectQuotaLimit(
            tenant_id=scope.tenant_id, project_id=scope.project_id,
            dimension=dimension, limit_value=int(limit_value),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            pass
        row = _project_limit(db, scope, dimension, lock=True)
    usage = _get_or_create_usage(db, ProjectQuotaUsage, scope, dimension)
    if int(limit_value) < int(usage.used) + int(usage.reserved):
        fail(409, 'QUOTA_LIMIT_BELOW_COMMITTED_USAGE',
             'Quota limit cannot be lower than committed plus reserved usage')
    row.limit_value = int(limit_value)
    return row


def set_tenant_limit(db, scope: Scope, dimension: str, limit_value: int):
    dimension = _dimension(dimension)
    _lock_scope(db, scope)
    _ensure_limit_accountable(db, scope, dimension, tenant_wide=True)
    if isinstance(limit_value, bool) or int(limit_value) < 0:
        fail(422, 'INVALID_QUOTA_VALUE', 'Quota limit must be a non-negative integer')
    row = _tenant_limit(db, scope, dimension, lock=True)
    if row is None:
        candidate = TenantQuotaLimit(
            tenant_id=scope.tenant_id, dimension=dimension, limit_value=int(limit_value),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            pass
        row = _tenant_limit(db, scope, dimension, lock=True)
    usage = _get_or_create_usage(db, TenantQuotaUsage, scope, dimension)
    if int(limit_value) < int(usage.used) + int(usage.reserved):
        fail(409, 'QUOTA_LIMIT_BELOW_COMMITTED_USAGE',
             'Quota limit cannot be lower than committed plus reserved usage')
    row.limit_value = int(limit_value)
    return row


def clear_project_limit(db, scope: Scope, dimension: str):
    dimension = _dimension(dimension)
    _lock_scope(db, scope)
    row = _project_limit(db, scope, dimension, lock=True)
    if row is not None:
        db.delete(row)
    return row


def clear_tenant_limit(db, scope: Scope, dimension: str):
    dimension = _dimension(dimension)
    _lock_scope(db, scope)
    row = _tenant_limit(db, scope, dimension, lock=True)
    if row is not None:
        db.delete(row)
    return row


def check_delta(db, scope: Scope, deltas: Mapping[str, int]):
    deltas = _values(deltas)
    result = []
    for dimension in sorted(deltas):
        increase = max(0, deltas[dimension])
        tenant_limit = _tenant_limit(db, scope, dimension)
        project_limit = _project_limit(db, scope, dimension)
        tenant_usage = _usage_read(db, TenantQuotaUsage, scope, dimension)
        project_usage = _usage_read(db, ProjectQuotaUsage, scope, dimension)
        tenant_total = int((tenant_usage.used + tenant_usage.reserved) if tenant_usage else 0)
        project_total = int((project_usage.used + project_usage.reserved) if project_usage else 0)
        allowed = True
        if tenant_limit is not None and tenant_total + increase > tenant_limit.limit_value:
            allowed = False
        if project_limit is not None and project_total + increase > project_limit.limit_value:
            allowed = False
        result.append({
            'dimension': dimension,
            'delta': deltas[dimension],
            'allowed': allowed,
            'tenant_after_reserve': tenant_total + increase,
            'project_after_reserve': project_total + increase,
            'tenant_limit': tenant_limit.limit_value if tenant_limit else None,
            'project_limit': project_limit.limit_value if project_limit else None,
        })
    return result


def reserve(db, quota: QuotaDelta, request_key: str, created_by: int | None):
    deltas = _values(quota.deltas)
    if not deltas:
        return None
    request_key = str(request_key)[:128]
    _lock_scope(db, quota.scope)
    existing = db.scalar(select(QuotaReservation).where(QuotaReservation.request_key == request_key).with_for_update())
    if existing is not None:
        if (_scope(existing) != quota.scope or existing.subject_type != quota.subject_type
                or existing.subject_id != str(quota.subject_id) or _values(existing.deltas) != deltas):
            fail(409, 'QUOTA_IDEMPOTENCY_CONFLICT', 'Quota reservation key was reused for different work')
        if existing.status != 'released':
            return existing
        positive = _positive(deltas)
        for dimension in sorted(deltas):
            tenant_limit = _tenant_limit(db, quota.scope, dimension, lock=True)
            project_limit = _project_limit(db, quota.scope, dimension, lock=True)
            tenant_usage = _get_or_create_usage(db, TenantQuotaUsage, quota.scope, dimension)
            project_usage = _get_or_create_usage(db, ProjectQuotaUsage, quota.scope, dimension)
            increase = positive.get(dimension, 0)
            if tenant_limit is not None and tenant_usage.used + tenant_usage.reserved + increase > tenant_limit.limit_value:
                fail(409, 'TENANT_QUOTA_EXCEEDED', f'Tenant quota exceeded for {dimension}')
            if project_limit is not None and project_usage.used + project_usage.reserved + increase > project_limit.limit_value:
                fail(409, 'PROJECT_QUOTA_EXCEEDED', f'Project quota exceeded for {dimension}')
            tenant_usage.reserved += increase
            project_usage.reserved += increase
        existing.status = 'reserved'
        existing.reconciliation_required = False
        existing.released_at = None
        existing.reconciled_at = None
        _ledger(db, existing, 're_reserved', deltas)
        return existing

    conflicting = db.scalar(select(QuotaReservation.id).where(
        QuotaReservation.tenant_id == quota.scope.tenant_id,
        QuotaReservation.project_id == quota.scope.project_id,
        QuotaReservation.subject_type == quota.subject_type,
        QuotaReservation.subject_id == str(quota.subject_id),
        QuotaReservation.status.in_(ACTIVE_RESERVATION_STATES),
    ).with_for_update())
    if conflicting is not None:
        fail(409, 'QUOTA_RECONCILIATION_REQUIRED',
             'The resource already has an active or uncertain quota reservation')

    positive = _positive(deltas)
    for dimension in sorted(deltas):
        tenant_limit = _tenant_limit(db, quota.scope, dimension, lock=True)
        project_limit = _project_limit(db, quota.scope, dimension, lock=True)
        tenant_usage = _get_or_create_usage(db, TenantQuotaUsage, quota.scope, dimension)
        project_usage = _get_or_create_usage(db, ProjectQuotaUsage, quota.scope, dimension)
        increase = positive.get(dimension, 0)
        if tenant_limit is not None and tenant_usage.used + tenant_usage.reserved + increase > tenant_limit.limit_value:
            fail(409, 'TENANT_QUOTA_EXCEEDED', f'Tenant quota exceeded for {dimension}')
        if project_limit is not None and project_usage.used + project_usage.reserved + increase > project_limit.limit_value:
            fail(409, 'PROJECT_QUOTA_EXCEEDED', f'Project quota exceeded for {dimension}')
        tenant_usage.reserved += increase
        project_usage.reserved += increase

    row = QuotaReservation(
        tenant_id=quota.scope.tenant_id,
        project_id=quota.scope.project_id,
        request_key=request_key,
        subject_type=quota.subject_type,
        subject_id=str(quota.subject_id),
        operation=quota.operation,
        deltas=deltas,
        status='reserved',
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    _ledger(db, row, 'reserved', deltas)
    return row


def _lock_reservation(db, reservation_id: str):
    snapshot = db.get(QuotaReservation, reservation_id)
    if snapshot is None:
        return None
    _lock_scope(db, _scope(snapshot))
    return db.scalar(select(QuotaReservation).where(
        QuotaReservation.id == reservation_id
    ).execution_options(populate_existing=True).with_for_update())


def commit_reservation(db, reservation_id: str):
    row = _lock_reservation(db, reservation_id)
    if row is None:
        return None
    if row.status == 'committed':
        return row
    if row.status == 'released':
        fail(409, 'QUOTA_RESERVATION_RELEASED', 'Released quota reservation cannot be committed')
    scope = _scope(row)
    deltas = _values(row.deltas)
    positive = _positive(deltas)
    allocation = _allocation(db, scope, row.subject_type, row.subject_id, lock=True)
    current = _values(allocation.dimensions if allocation else {}, signed=False)
    target = dict(current)

    for dimension in sorted(deltas):
        tenant_usage = _get_or_create_usage(db, TenantQuotaUsage, scope, dimension)
        project_usage = _get_or_create_usage(db, ProjectQuotaUsage, scope, dimension)
        reserved = positive.get(dimension, 0)
        if tenant_usage.reserved < reserved or project_usage.reserved < reserved:
            fail(409, 'QUOTA_ACCOUNTING_CONFLICT', 'Quota reservation counters are inconsistent')
        next_tenant = int(tenant_usage.used) + deltas[dimension]
        next_project = int(project_usage.used) + deltas[dimension]
        next_subject = int(current.get(dimension, 0)) + deltas[dimension]
        if min(next_tenant, next_project, next_subject) < 0:
            fail(409, 'QUOTA_ACCOUNTING_CONFLICT', 'Quota usage cannot become negative')
        tenant_usage.reserved -= reserved
        project_usage.reserved -= reserved
        tenant_usage.used = next_tenant
        project_usage.used = next_project
        if next_subject:
            target[dimension] = next_subject
        else:
            target.pop(dimension, None)

    if target:
        if allocation is None:
            allocation = QuotaAllocation(
                tenant_id=scope.tenant_id, project_id=scope.project_id,
                subject_type=row.subject_type, subject_id=row.subject_id,
                dimensions=target,
            )
            db.add(allocation)
        else:
            allocation.dimensions = target
    elif allocation is not None:
        db.delete(allocation)

    was_uncertain = row.status == 'uncertain'
    row.status = 'committed'
    row.reconciliation_required = False
    row.committed_at = now()
    if was_uncertain:
        row.reconciled_at = now()
    _ledger(db, row, 'committed', deltas)
    return row


def release_reservation(db, reservation_id: str, *, reconciled: bool = False):
    row = _lock_reservation(db, reservation_id)
    if row is None or row.status == 'released':
        return row
    if row.status == 'committed':
        return row
    scope = _scope(row)
    positive = _positive(_values(row.deltas))
    for dimension in sorted(positive):
        tenant_usage = _get_or_create_usage(db, TenantQuotaUsage, scope, dimension)
        project_usage = _get_or_create_usage(db, ProjectQuotaUsage, scope, dimension)
        value = positive[dimension]
        if tenant_usage.reserved < value or project_usage.reserved < value:
            fail(409, 'QUOTA_ACCOUNTING_CONFLICT', 'Quota reservation counters are inconsistent')
        tenant_usage.reserved -= value
        project_usage.reserved -= value
    row.status = 'released'
    row.reconciliation_required = False
    row.released_at = now()
    if reconciled:
        row.reconciled_at = now()
    _ledger(db, row, 'released', row.deltas or {})
    return row


def mark_uncertain(db, reservation_id: str):
    row = _lock_reservation(db, reservation_id)
    if row is None or row.status in {'committed', 'released'}:
        return row
    row.status = 'uncertain'
    row.reconciliation_required = True
    _ledger(db, row, 'uncertain', row.deltas or {})
    return row


def reconcile_reservation(db, reservation_id: str, outcome: str):
    row = _lock_reservation(db, reservation_id)
    if row is None:
        fail(404, 'QUOTA_RESERVATION_NOT_FOUND', 'Quota reservation not found')
    if row.status != 'uncertain':
        fail(409, 'QUOTA_RECONCILIATION_NOT_REQUIRED', 'Reservation is not awaiting reconciliation')
    if outcome == 'commit':
        result = commit_reservation(db, row.id)
        result.reconciled_at = now()
        return result
    if outcome == 'release':
        return release_reservation(db, row.id, reconciled=True)
    fail(422, 'INVALID_RECONCILIATION_OUTCOME', 'Outcome must be commit or release')


def _provider_unresolved_dimensions(provider: str) -> set[str]:
    provider = str(provider or '').lower()
    if provider in {'proxmox', 'vmware'}:
        return set()
    if provider in {'aws', 'azure'}:
        return {'vcpu', 'memory_mb'}
    if provider == 'openstack':
        return {'vcpu', 'memory_mb', 'disk_gib'}
    return {'vcpu', 'memory_mb', 'disk_gib'}


def deployment_unresolved_dimensions(deployment: Deployment) -> set[str]:
    return _provider_unresolved_dimensions(deployment.provider)


def deployment_dimensions(deployment: Deployment) -> dict[str, int]:
    values = dict(deployment.variables or {})
    provider = str(deployment.provider or '').lower()
    result = {'vm_count': 1}
    if provider in {'proxmox', 'vmware'}:
        cpu = _nonnegative_int(values.get('cpu'))
        memory = _nonnegative_int(values.get('memory'))
        disk = _nonnegative_int(values.get('disk'))
        if cpu:
            result['vcpu'] = cpu
        if memory:
            result['memory_mb'] = memory
        if disk:
            result['disk_gib'] = disk
    elif provider == 'aws':
        disk = _nonnegative_int(values.get('root_volume_size'))
        if disk:
            result['disk_gib'] = disk
    elif provider == 'azure':
        disk = _nonnegative_int(values.get('os_disk_size_gb'))
        if disk:
            result['disk_gib'] = disk
    return result


def active_limit_dimensions(db, scope: Scope) -> set[str]:
    return {
        dimension
        for dimension in DIMENSIONS
        if _tenant_limit(db, scope, dimension) is not None
        or _project_limit(db, scope, dimension) is not None
    }


def require_governed_legacy_mutation(db, scope: Scope, dimensions, action: str):
    blocked = sorted(active_limit_dimensions(db, scope) & set(dimensions))
    if blocked:
        fail(
            409,
            'QUOTA_GOVERNED_ACTION_REQUIRED',
            f"{action} must use the governed Day-2/provisioning path while quota is active for: {', '.join(blocked)}",
        )


def _ensure_limit_accountable(db, scope: Scope, dimension: str, *, tenant_wide: bool):
    if dimension == 'vm_count':
        return
    table = Deployment.__table__
    query = select(table.c.id, table.c.provider).where(
        table.c.tenant_id == scope.tenant_id,
        table.c.destroyed_at.is_(None),
    )
    if not tenant_wide:
        query = query.where(table.c.project_id == scope.project_id)
    unresolved = [
        row.id for row in db.connection().execute(query)
        if dimension in _provider_unresolved_dimensions(row.provider)
    ]
    if unresolved:
        fail(
            409,
            'QUOTA_DIMENSION_UNRESOLVED',
            f'Cannot enable {dimension} quota while active deployments use provider shapes '
            f'that do not expose a normalized {dimension} value',
        )


def _job_quota(db, job: Job, deployment: Deployment):
    scope = Scope(job.tenant_id, job.project_id)
    current = allocation_dimensions(db, scope, 'deployment', deployment.id)
    if job.operation in {'terraform.apply', 'terraform.import'}:
        unresolved = active_limit_dimensions(db, scope) & deployment_unresolved_dimensions(deployment)
        if unresolved:
            fail(
                409,
                'QUOTA_DIMENSION_UNRESOLVED',
                'Quota admission cannot determine normalized capacity for: ' + ', '.join(sorted(unresolved)),
            )
        target = deployment_dimensions(deployment)
    elif job.operation == 'terraform.destroy':
        target = {}
    else:
        return None
    deltas = {key: int(target.get(key, 0)) - int(current.get(key, 0))
              for key in DIMENSIONS if int(target.get(key, 0)) != int(current.get(key, 0))}
    return QuotaDelta(scope, 'deployment', deployment.id, job.operation, deltas)


def prepare_job_reservation(db, job: Job, deployment: Deployment | None = None):
    if job.operation not in MUTATING_TERRAFORM or not job.deployment_id:
        return None
    deployment = deployment or db.get(Deployment, job.deployment_id)
    if deployment is None:
        return None
    quota = _job_quota(db, job, deployment)
    if quota is None or not quota.deltas:
        payload = dict(job.payload or {})
        payload['_quota_checked'] = True
        job.payload = payload
        return None
    reservation = reserve(db, quota, f'job:{job.id}', job.created_by)
    payload = dict(job.payload or {})
    payload['_quota_checked'] = True
    payload['_quota_reservation_id'] = reservation.id
    job.payload = payload
    return reservation


def job_reservation(db, job: Job):
    reservation_id = (job.payload or {}).get('_quota_reservation_id')
    if reservation_id:
        return db.get(QuotaReservation, reservation_id)
    return db.scalar(select(QuotaReservation).where(QuotaReservation.request_key == f'job:{job.id}'))


def commit_job_reservation(db, job: Job):
    row = job_reservation(db, job)
    return commit_reservation(db, row.id) if row else None


def release_job_reservation(db, job: Job):
    row = job_reservation(db, job)
    return release_reservation(db, row.id) if row else None


def mark_job_reservation_uncertain(db, job: Job):
    row = job_reservation(db, job)
    return mark_uncertain(db, row.id) if row else None


def release_active_for_subject(db, scope: Scope, subject_type: str, subject_id: str):
    _lock_scope(db, scope)
    rows = db.scalars(select(QuotaReservation).where(
        QuotaReservation.tenant_id == scope.tenant_id,
        QuotaReservation.project_id == scope.project_id,
        QuotaReservation.subject_type == subject_type,
        QuotaReservation.subject_id == str(subject_id),
        QuotaReservation.status.in_(ACTIVE_RESERVATION_STATES),
    ).with_for_update()).all()
    for row in rows:
        release_reservation(db, row.id, reconciled=(row.status == 'uncertain'))
    return len(rows)


def reconcile_terraform_presence(db, scope: Scope, subject_id: str, *, present: bool):
    """Resolve Terraform reservations from independently confirmed resource presence.

    Presence is enough to conservatively commit an apply reservation (possibly
    over-counting a partial apply, which is safer than freeing capacity). Absence
    releases apply reservations and commits destroy reservations. Day-2
    reservations are intentionally excluded because mere VM presence does not
    prove a resize/disk mutation succeeded.
    """
    _lock_scope(db, scope)
    rows = db.scalars(select(QuotaReservation).where(
        QuotaReservation.tenant_id == scope.tenant_id,
        QuotaReservation.project_id == scope.project_id,
        QuotaReservation.subject_type == 'deployment',
        QuotaReservation.subject_id == str(subject_id),
        QuotaReservation.status.in_(ACTIVE_RESERVATION_STATES),
        QuotaReservation.operation.in_(('terraform.apply', 'terraform.destroy')),
    ).order_by(QuotaReservation.created_at).with_for_update()).all()
    resolved = []
    allocation = _allocation(db, scope, 'deployment', str(subject_id), lock=True)
    for row in rows:
        if row.operation == 'terraform.apply':
            # Presence proves an initial create reached the provider, but it
            # does not prove a re-apply changed CPU/RAM/disk to the requested
            # target. Keep update reservations unresolved until stronger
            # provider/state evidence is available.
            if allocation is not None:
                continue
            resolved.append(
                commit_reservation(db, row.id) if present
                else release_reservation(db, row.id, reconciled=(row.status == 'uncertain'))
            )
            if present:
                allocation = _allocation(db, scope, 'deployment', str(subject_id), lock=True)
        else:
            resolved.append(
                release_reservation(db, row.id, reconciled=(row.status == 'uncertain')) if present
                else commit_reservation(db, row.id)
            )
    return resolved


def account_confirmed_absent(db, scope: Scope, subject_type: str, subject_id: str,
                             request_key: str, created_by: int | None):
    # A confirmed destroy is stronger evidence than a stale/uncertain create.
    # Release every active positive reservation first, then remove committed usage.
    release_active_for_subject(db, scope, subject_type, subject_id)
    current = allocation_dimensions(db, scope, subject_type, subject_id)
    if not current:
        return None
    quota = QuotaDelta(
        scope, subject_type, str(subject_id), 'reconcile.confirmed_absent',
        {key: -value for key, value in current.items()},
    )
    row = reserve(db, quota, request_key, created_by)
    return commit_reservation(db, row.id) if row else None


def _target_scope_and_subject(db, target):
    row = db.get(ManagedVM, target.resource_id)
    if row is None:
        row = db.get(ManagedResource, target.resource_id)
    if row is None:
        fail(404, 'RESOURCE_NOT_FOUND', 'Resource not found for quota accounting')
    scope = Scope(row.tenant_id, row.project_id)
    return scope, 'deployment' if row.deployment_id else 'resource', str(row.deployment_id or row.id)


def _quota_limits_exist(db, scope: Scope):
    return bool(active_limit_dimensions(db, scope))


def day2_delta(db, target, action: str, params: Mapping, current: Mapping | None = None):
    scope, subject_type, subject_id = _target_scope_and_subject(db, target)
    allocation = allocation_dimensions(db, scope, subject_type, subject_id)
    action = str(action)
    deltas: dict[str, int] = {}
    if action == 'resize_compute':
        if not allocation and _quota_limits_exist(db, scope):
            fail(409, 'QUOTA_ALLOCATION_REQUIRED',
                 'Resource must be adopted into quota accounting before compute resize')
        if 'cpu_cores' in params or 'cpu_sockets' in params:
            live = dict(current or {})
            cores = int(params.get('cpu_cores', live.get('cpu_cores') or allocation.get('vcpu') or 1))
            sockets = int(params.get('cpu_sockets', live.get('cpu_sockets') or 1))
            requested = max(0, cores * sockets)
            deltas['vcpu'] = requested - int(allocation.get('vcpu', requested))
        if 'memory_mb' in params:
            requested = max(0, int(params['memory_mb']))
            deltas['memory_mb'] = requested - int(allocation.get('memory_mb', requested))
    elif action in {'add_disk', 'resize_disk', 'delete_disk'}:
        if not allocation and _quota_limits_exist(db, scope):
            fail(409, 'QUOTA_ALLOCATION_REQUIRED',
                 'Resource must be adopted into quota accounting before disk changes')
        live = dict(current or {})
        if action == 'add_disk':
            size = int(params.get('size_gib') or 0)
            deltas['disk_gib'] = max(0, size)
        elif action == 'resize_disk':
            requested = int(params.get('new_size_gib') or 0)
            existing = int(live.get('new_size_gib') or 0)
            deltas['disk_gib'] = max(0, requested - existing)
        else:
            size = int(live.get('size_gib') or 0)
            deltas['disk_gib'] = -max(0, size)
    elif action == 'delete_vm':
        deltas = {key: -value for key, value in allocation.items()}
    elif action == 'clone_vm':
        fail(409, 'QUOTA_GOVERNED_CLONE_REQUIRED',
             'Clone must use governed provisioning so the new resource receives its own quota allocation')
    return QuotaDelta(scope, subject_type, subject_id, 'day2.' + action,
                      {key: value for key, value in deltas.items() if value})


def check_day2(db, target, action: str, params: Mapping, current: Mapping | None = None):
    quota = day2_delta(db, target, action, params, current=current)
    checks = check_delta(db, quota.scope, quota.deltas)
    denied = next((item for item in checks if not item['allowed']), None)
    if denied:
        tenant_exceeded = (
            denied['tenant_limit'] is not None
            and denied['tenant_after_reserve'] > denied['tenant_limit']
        )
        project_exceeded = (
            denied['project_limit'] is not None
            and denied['project_after_reserve'] > denied['project_limit']
        )
        code = 'TENANT_QUOTA_EXCEEDED' if tenant_exceeded else (
            'PROJECT_QUOTA_EXCEEDED' if project_exceeded else 'QUOTA_ACCOUNTING_CONFLICT'
        )
        fail(409, code, f"Quota exceeded for {denied['dimension']}")
    return quota, checks


def prepare_day2_reservation(db, job: Job, target, action: str, params: Mapping, current: Mapping | None = None):
    quota, _ = check_day2(db, target, action, params, current=current)
    if not quota.deltas:
        payload = dict(job.payload or {})
        payload['_quota_checked'] = True
        job.payload = payload
        return None
    reservation = reserve(db, quota, f'job:{job.id}', job.created_by)
    payload = dict(job.payload or {})
    payload['_quota_checked'] = True
    payload['_quota_reservation_id'] = reservation.id
    job.payload = payload
    return reservation
