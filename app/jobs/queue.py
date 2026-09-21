import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.job import Job as RQJob
from rq.exceptions import NoSuchJobError
from rq.serializers import JSONSerializer
from sqlalchemy import select, update
from app.config import settings
from app.blueprint_settings import blueprint_execution_settings
from app.database import session
from app.inventory_sync import repair_inventory_from_states
from app.models import Deployment, HostnameReservation, IPAllocation, Job, JobLog, ManagedResource, ManagedVM, now
from app.operations.service import cleanup_retention_once, deliver_webhooks_once, materialize_scheduled_jobs, queue_job_webhooks, queue_system_alert_webhooks_once
from app.security.core import redis_client
from app.terraform.state import delete_plan


@lru_cache
def queue_connection():
    # RQ blocks waiting for work for up to worker_ttl - 15 seconds. The API's
    # short Redis timeout must not interrupt this blocking dequeue operation.
    return Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=100)


def queue():
    return Queue('cloudportal', connection=queue_connection(), serializer=JSONSerializer)


def provider_retry_ready(job):
    wait = (job.payload or {}).get('_provider_wait') or {}
    raw = wait.get('next_attempt_at')
    if not raw:
        return True
    try:
        return datetime.fromisoformat(raw) <= now()
    except (TypeError, ValueError):
        return True


CANCELLATION_GRACE_SECONDS = 60


def reconcile_cancelled_jobs(db):
    threshold = now() - timedelta(seconds=CANCELLATION_GRACE_SECONDS)
    rows = db.scalars(
        select(Job).where(
            Job.cancel_requested.is_(True),
            Job.status.in_(['running', 'cancelling']),
        ).with_for_update(skip_locked=True).limit(200)
    ).all()
    for job in rows:
        if job.status == 'running':
            job.status = 'cancelling'
        deployment = db.get(Deployment, job.deployment_id) if job.deployment_id else None
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'cancelling'

        last_seen = job.heartbeat_at or job.updated_at or job.created_at
        if last_seen is not None and last_seen > threshold:
            continue

        try:
            rq_job = RQJob.fetch(job.id, connection=redis_client(), serializer=JSONSerializer)
            rq_status = rq_job.get_status(refresh=True)
            if rq_status in {'queued', 'deferred', 'scheduled'}:
                continue
            if rq_status == 'started':
                rq_last_seen = getattr(rq_job, 'last_heartbeat', None) or getattr(rq_job, 'started_at', None)
                if rq_last_seen is None:
                    continue
                if getattr(rq_last_seen, 'tzinfo', None) is not None:
                    rq_last_seen = rq_last_seen.astimezone(timezone.utc).replace(tzinfo=None)
                if rq_last_seen > threshold:
                    continue
        except NoSuchJobError:
            pass
        except RedisError:
            # Redis uncertainty is not proof that the worker stopped.
            continue

        job.status = 'cancelled'
        job.error = 'Cancellation requested; worker no longer owns an active execution'
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.active_job_id = None
            deployment.status = 'cancelled'
        db.add(JobLog(job_id=job.id, message='job.cancelled: anulowanie zakończone przez dispatcher'))
        queue_job_webhooks(db, job)


def reconcile_persisted_inventory(db):
    result = repair_inventory_from_states(db, limit=200)
    for item in result['repaired']:
        deployment = db.get(Deployment, item['deployment_id'])
        if deployment is None or not deployment.active_job_id:
            continue
        job = db.get(Job, deployment.active_job_id)
        if job is None:
            continue
        if item['vm_id'] is not None:
            message = f"inventory.vm.recovered: {item['node']} / VMID {item['vm_id']}"
        else:
            message = f"inventory.resource.recovered: {item['external_id']}"
        db.add(JobLog(job_id=job.id, message=message))


def reconcile_deployment_job_statuses(db):
    deployments = db.scalars(
        select(Deployment).where(Deployment.active_job_id.is_not(None)).with_for_update(skip_locked=True).limit(200)
    ).all()
    for deployment in deployments:
        job = db.get(Job, deployment.active_job_id)
        if job is None:
            deployment.active_job_id = None
            deployment.status = 'failed'
            continue
        if job.status == 'cancelling' or job.cancel_requested:
            deployment.status = 'cancelling'
            continue
        if job.status == 'running':
            if deployment.status not in {'waiting_provider', 'recovery_queued'}:
                deployment.status = 'running'
            continue
        if job.status == 'queued':
            if deployment.status not in {'waiting_provider', 'recovery_queued'}:
                deployment.status = 'queued'
            continue
        if job.status not in {'successful', 'failed', 'cancelled'}:
            continue

        deployment.active_job_id = None
        if job.operation == 'terraform.plan':
            deployment.status = (job.payload or {}).get('previous_status', deployment.status or 'failed')
        elif job.status != 'successful':
            deployment.status = job.status
        elif job.operation == 'terraform.destroy':
            deployment.status = 'destroyed'
            if deployment.destroyed_at is None:
                deployment.destroyed_at = now()
        elif job.operation == 'terraform.import':
            deployment.status = 'imported'
        else:
            deployment.status = 'successful'


def expire_waiting_approvals(db):
    config = blueprint_execution_settings(db)
    rows = db.scalars(
        select(Job).where(
            Job.status == 'waiting_approval',
            Job.cancel_requested.is_(False),
        ).with_for_update(skip_locked=True).limit(200)
    ).all()
    current_time = now()
    for job in rows:
        payload = dict(job.payload or {})
        approval = dict(payload.get('_approval') or {})
        raw_expiry = approval.get('expires_at')
        if raw_expiry:
            try:
                expires_at = datetime.fromisoformat(raw_expiry)
            except (TypeError, ValueError):
                expires_at = current_time
        else:
            basis = job.updated_at or job.created_at or current_time
            expires_at = basis + timedelta(hours=config['approval_timeout_hours'])
        if expires_at > current_time:
            continue

        deployment = db.get(Deployment, job.deployment_id) if job.deployment_id else None
        has_resource = False
        if deployment is not None:
            has_resource = bool(
                db.scalar(select(ManagedResource.id).where(
                    ManagedResource.deployment_id == deployment.id,
                    ManagedResource.lifecycle_status == 'active',
                ).limit(1))
                or db.scalar(select(ManagedVM.id).where(
                    ManagedVM.deployment_id == deployment.id,
                    ManagedVM.lifecycle_status == 'active',
                ).limit(1))
            )

        if deployment is not None:
            delete_plan(deployment.id)

        if has_resource:
            job.status = 'failed'
            job.error = (
                'Approval request expired after infrastructure state was created; '
                'inspect the deployment and clean up manually'
            )
            if deployment is not None and deployment.active_job_id == job.id:
                deployment.active_job_id = None
                deployment.status = 'failed'
        else:
            job.status = 'cancelled'
            job.error = 'Approval request expired'
            if deployment is not None and deployment.active_job_id == job.id:
                deployment.active_job_id = None
                deployment.status = 'cancelled'
                released_at = current_time
                db.execute(update(HostnameReservation).where(
                    HostnameReservation.resource_id == deployment.id,
                    HostnameReservation.status != 'released',
                ).values(status='released', released_at=released_at))
                db.execute(update(IPAllocation).where(
                    IPAllocation.resource_id == deployment.id,
                    IPAllocation.status != 'released',
                ).values(status='released', released_at=released_at))

        db.add(JobLog(job_id=job.id, message='workflow.approval.expired: ' + job.error))
        queue_job_webhooks(db, job)


def dispatch_once():
    materialize_scheduled_jobs()
    q = queue()
    with session() as db:
        expire_waiting_approvals(db)
        jobs = db.scalars(select(Job).where(Job.status == 'queued', Job.cancel_requested.is_(False))
                          .with_for_update(skip_locked=True).limit(100)).all()
        for job in jobs:
            if not provider_retry_ready(job):
                continue
            try:
                existing = RQJob.fetch(job.id, connection=redis_client(), serializer=JSONSerializer)
                if existing.get_status(refresh=True) in {'queued', 'started', 'deferred', 'scheduled'}:
                    continue
                existing.delete()
            except NoSuchJobError:
                pass
            q.enqueue('app.jobs.worker.execute', job.id, job_id=job.id,
                      job_timeout=settings().execution_timeout + 120, result_ttl=86400, failure_ttl=86400)
            job.dispatched_at = now()
        reconcile_cancelled_jobs(db)
        reconcile_deployment_job_statuses(db)
        reconcile_persisted_inventory(db)
        # Fail uncertain interrupted executions conservatively instead of risking a duplicate apply.
        stale = db.scalars(select(Job).where(
            Job.status == 'running',
            Job.cancel_requested.is_(False),
            Job.heartbeat_at < now() - timedelta(seconds=settings().execution_timeout + 180),
        ).with_for_update(skip_locked=True)).all()
        for job in stale:
            job.status = 'failed'
            job.error = 'Worker heartbeat lost. Inventory was reconciled from persisted Terraform state; inspect state before retrying.'
            if job.deployment_id:
                d = db.get(Deployment, job.deployment_id)
                d.active_job_id, d.status = None, 'failed'
            db.add(JobLog(job_id=job.id, message=job.error))
            queue_job_webhooks(db, job)
        db.commit()
    queue_system_alert_webhooks_once()
    deliver_webhooks_once()
    cleanup_retention_once()
    redis_client().set('cp:dispatcher:heartbeat', 'alive', ex=30)


def dispatch_forever():
    while True:
        try:
            dispatch_once()
        except Exception:
            # Keep pending jobs in PostgreSQL for the next dispatch cycle; no payload logging.
            print('Dispatcher unavailable; jobs remain in the durable queue', flush=True)
        time.sleep(2)


def work():
    Worker([queue()], connection=queue_connection(), serializer=JSONSerializer,
           worker_ttl=90, job_monitoring_interval=15).work()


if __name__ == '__main__':
    import sys
    dispatch_forever() if '--dispatcher' in sys.argv else work()
