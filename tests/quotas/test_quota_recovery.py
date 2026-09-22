"""Quota recovery after dispatcher-owned worker interruption."""
from datetime import timedelta

from app.config import settings
from app.database import session
from app.jobs.queue import reconcile_stale_jobs
from app.models import Job, now
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.quotas.models import ProjectQuotaUsage, QuotaReservation
from app.quotas.service import QuotaDelta, reconcile_reservation, reserve, set_project_limit
from app.resource_scope.authorization import Scope
from app.tenancy.permissions import DEFAULT_TENANT_ID


SCOPE = Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)


def test_stale_worker_marks_reserved_quota_uncertain_and_reconcilable(system):
    with session() as db:
        set_project_limit(db, SCOPE, 'vm_count', 1)
        reservation = reserve(
            db,
            QuotaDelta(SCOPE, 'deployment', 'stale-deployment', 'terraform.apply', {'vm_count': 1}),
            'job:stale-quota',
            1,
        )
        job = Job(
            id='stale-quota',
            tenant_id=SCOPE.tenant_id,
            project_id=SCOPE.project_id,
            operation='terraform.apply',
            payload={'_quota_reservation_id': reservation.id},
            status='running',
            created_by=1,
            request_id='00000000-0000-0000-0000-000000000001',
            source='API',
            heartbeat_at=now() - timedelta(seconds=settings().execution_timeout + 181),
        )
        db.add(job)
        db.commit()

    with session() as db:
        assert reconcile_stale_jobs(db) == 1
        db.commit()

    with session() as db:
        job = db.get(Job, 'stale-quota')
        reservation = db.get(QuotaReservation, job.payload['_quota_reservation_id'])
        usage = db.query(ProjectQuotaUsage).filter_by(
            tenant_id=SCOPE.tenant_id,
            project_id=SCOPE.project_id,
            dimension='vm_count',
        ).one()
        assert job.status == 'failed'
        assert reservation.status == 'uncertain'
        assert reservation.reconciliation_required is True
        assert (usage.used, usage.reserved) == (0, 1)

        reconcile_reservation(db, reservation.id, 'release')
        db.commit()

    with session() as db:
        usage = db.query(ProjectQuotaUsage).filter_by(
            tenant_id=SCOPE.tenant_id,
            project_id=SCOPE.project_id,
            dimension='vm_count',
        ).one()
        assert (usage.used, usage.reserved) == (0, 0)
