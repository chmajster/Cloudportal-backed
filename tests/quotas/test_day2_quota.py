"""Day-2 quota recovery gates: reserve, commit, cancel and uncertain provider outcomes."""
from sqlalchemy import select

from app.database import session
from app.day2 import worker
from app.day2.models import Day2ActionRequest
from app.models import Job, ManagedVM
from app.quotas.models import ProjectQuotaUsage, QuotaAllocation, QuotaReservation
from app.quotas.service import (
    QuotaDelta,
    commit_reservation,
    reserve,
    set_project_limit,
)
from app.resource_scope.authorization import Scope
from test_day2 import key, submit


def seed_memory(resource, *, limit=4096):
    vm_id = resource[2]
    with session() as db:
        vm = db.get(ManagedVM, vm_id)
        scope = Scope(vm.tenant_id, vm.project_id)
        set_project_limit(db, scope, 'memory_mb', limit)
        baseline = reserve(
            db,
            QuotaDelta(scope, 'resource', vm_id, 'adopt', {'memory_mb': 2048}),
            'day2-quota-baseline:' + vm_id,
            vm.created_by,
        )
        commit_reservation(db, baseline.id)
        db.commit()
    return scope


def memory_usage(db, scope):
    return db.scalar(select(ProjectQuotaUsage).where(
        ProjectQuotaUsage.tenant_id == scope.tenant_id,
        ProjectQuotaUsage.project_id == scope.project_id,
        ProjectQuotaUsage.dimension == 'memory_mb',
    ))


def test_day2_positive_delta_is_rejected_before_job_when_quota_is_exhausted(resource):
    scope = seed_memory(resource, limit=3072)
    client, headers, _, fake = resource

    response = submit(resource, key(headers), action='resize_compute', params={'memory_mb': 4096})

    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'PROJECT_QUOTA_EXCEEDED'
    assert fake.calls == []
    with session() as db:
        usage = memory_usage(db, scope)
        assert (usage.used, usage.reserved) == (2048, 0)
        assert db.scalar(select(QuotaReservation.id).where(
            QuotaReservation.operation == 'day2.resize_compute'
        )) is None


def test_day2_success_commits_reserved_delta_once(resource):
    scope = seed_memory(resource)
    client, headers, vm_id, fake = resource

    created = submit(resource, key(headers), action='resize_compute', params={'memory_mb': 4096})
    assert created.status_code == 202, created.text
    data = created.json()
    with session() as db:
        usage = memory_usage(db, scope)
        assert (usage.used, usage.reserved) == (2048, 2048)

    worker.execute(data['job_id'])
    worker.execute(data['job_id'])

    assert len(fake.calls) == 1
    with session() as db:
        usage = memory_usage(db, scope)
        assert (usage.used, usage.reserved) == (4096, 0)
        allocation = db.scalar(select(QuotaAllocation).where(
            QuotaAllocation.subject_type == 'resource',
            QuotaAllocation.subject_id == vm_id,
        ))
        assert allocation.dimensions['memory_mb'] == 4096
        reservation = db.get(QuotaReservation, db.get(Job, data['job_id']).payload['_quota_reservation_id'])
        assert reservation.status == 'committed'


def test_day2_cancel_before_provider_releases_reservation(resource):
    scope = seed_memory(resource)
    client, headers, _, fake = resource

    created = submit(resource, key(headers), action='resize_compute', params={'memory_mb': 4096})
    assert created.status_code == 202, created.text
    data = created.json()
    cancelled = client.post(
        f"/api/v1/day2-actions/{data['action_request_id']}/cancel",
        headers=key(headers),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == 'CANCELLED'
    assert fake.calls == []

    with session() as db:
        usage = memory_usage(db, scope)
        assert (usage.used, usage.reserved) == (2048, 0)
        job = db.get(Job, data['job_id'])
        reservation = db.get(QuotaReservation, job.payload['_quota_reservation_id'])
        assert reservation.status == 'released'


def test_day2_timeout_keeps_quota_reserved_and_marks_reconciliation_required(resource):
    scope = seed_memory(resource)
    client, headers, _, fake = resource
    fake.fail_wait = True

    created = submit(resource, key(headers), action='resize_compute', params={'memory_mb': 4096})
    assert created.status_code == 202, created.text
    data = created.json()
    worker.execute(data['job_id'])

    with session() as db:
        usage = memory_usage(db, scope)
        assert (usage.used, usage.reserved) == (2048, 2048)
        job = db.get(Job, data['job_id'])
        reservation = db.get(QuotaReservation, job.payload['_quota_reservation_id'])
        request = db.get(Day2ActionRequest, data['action_request_id'])
        assert reservation.status == 'uncertain'
        assert reservation.reconciliation_required is True
        assert request.result['reconciliation_required'] is True
