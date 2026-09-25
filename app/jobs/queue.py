import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.job import Job as RQJob
from rq.exceptions import NoSuchJobError
from rq.serializers import JSONSerializer
from sqlalchemy import func, select, text, update
from app.config import settings
from app.database import session
from app.events.service import dispatch_event_broker_once
from app.jobs.settings import job_execution_settings
from app.inventory_sync import repair_inventory_from_states
from app.instance_operation import normal_instance_operation
from app.jobs.approval import approval_policy_for_job
from app.jobs.recovery import queue_automatic_resume, record_persisted_state_recovery
from app.models import Deployment, HostnameReservation, IPAllocation, Job, JobLog, ManagedResource, ManagedVM, now
from app.operations.service import cleanup_retention_once, deliver_webhooks_once, materialize_scheduled_jobs, queue_job_webhooks, queue_system_alert_webhooks_once
from app.providers.task_reconcile import reconcile_proxmox_tasks_once
from app.security.core import redis_client
from app.terraform.state import delete_plan
from app.quotas.service import mark_job_reservation_uncertain


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
STALE_JOB_HEARTBEAT_SECONDS = 180
DISPATCH_CAPACITY_LOCK_KEY = 4850454325908757588


def acquire_dispatch_capacity_lock(db):
    # Serialize the capacity calculation + RQ enqueue batch across dispatcher
    # processes. The PostgreSQL transaction-scoped advisory lock is released
    # automatically by the commit at the end of this dispatch cycle.
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(
            text('SELECT pg_advisory_xact_lock(:lock_key)'),
            {'lock_key': DISPATCH_CAPACITY_LOCK_KEY},
        )


def parallel_dispatch_capacity(db, active_rq_jobs=0):
    parallel_limit = job_execution_settings(db)['max_parallel_jobs']
    running_jobs = db.scalar(
        select(func.count()).select_from(Job).where(
            Job.status.in_(['running', 'cancelling'])
        )
    ) or 0
    return max(0, parallel_limit - int(running_jobs) - int(active_rq_jobs))


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

        mark_job_reservation_uncertain(db, job)
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
        record_persisted_state_recovery(job, item)
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
            previous_status = (job.payload or {}).get(
                'previous_status',
                deployment.status or 'failed',
            )
            deployment.status = (
                'successful'
                if previous_status == 'reconciliation_required' and job.status == 'successful'
                else previous_status
            )
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
            config = approval_policy_for_job(db, job)
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

        runtime = dict(payload.get('_workflow_runtime') or {})
        provider_mutated = runtime.get('provider_applied') is True
        if has_resource and provider_mutated:
            job.status = 'failed'
            job.error = (
                'Approval request expired after this job created or changed infrastructure; '
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
                previous_status = str(payload.get('previous_status') or '').strip()
                if has_resource and previous_status and previous_status not in {
                    'queued', 'running', 'waiting_approval', 'cancelling'
                }:
                    # Re-apply approval can expire while a pre-existing healthy VM
                    # still exists. Restore its previous deployment status instead
                    # of falsely marking the resource itself as cancelled/failed.
                    deployment.status = previous_status
                else:
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


def reconcile_stale_jobs(db):
    rows = db.scalars(select(Job).where(
        Job.status == 'running',
        Job.cancel_requested.is_(False),
        # Heartbeats are refreshed by Context.check() during Terraform/Ansible
        # subprocesses and workflow polling. Do not reserve a concurrency slot for
        # an entire execution timeout after a worker has disappeared.
        Job.heartbeat_at < now() - timedelta(seconds=STALE_JOB_HEARTBEAT_SECONDS),
    ).with_for_update(skip_locked=True)).all()
    for job in rows:
        mark_job_reservation_uncertain(db, job)
        deployment = db.get(Deployment, job.deployment_id) if job.deployment_id else None
        resumed = queue_automatic_resume(db, job, deployment)

        job.status = 'failed'
        if resumed is not None:
            job.error = (
                'Worker heartbeat lost. Persisted Terraform state and quota were reconciled; '
                f'automatic resume queued as job {resumed.id}.'
            )
        else:
            job.error = (
                'Worker heartbeat lost. Inventory was reconciled from persisted Terraform state; '
                'quota reservation requires reconciliation before retrying.'
            )

        if (
            resumed is None
            and deployment is not None
            and deployment.active_job_id == job.id
        ):
            deployment.active_job_id = None
            deployment.status = 'failed'
        db.add(JobLog(job_id=job.id, message=job.error))
        queue_job_webhooks(db, job)
    return len(rows)


def _dispatch_once_unfenced():
    materialize_scheduled_jobs()
    q = queue()
    with session() as db:
        acquire_dispatch_capacity_lock(db)
        expire_waiting_approvals(db)
        jobs = db.scalars(
            select(Job)
            .where(Job.status == 'queued', Job.cancel_requested.is_(False))
            .order_by(Job.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()

        # A job already present in RQ reserves one concurrency slot even while
        # its PostgreSQL status is still "queued". Without this reservation a
        # fast dispatcher loop could enqueue another batch before workers have
        # time to atomically claim the first one.
        active_rq_jobs = set()
        for job in jobs:
            try:
                existing = RQJob.fetch(job.id, connection=redis_client(), serializer=JSONSerializer)
                if existing.get_status(refresh=True) in {'queued', 'started', 'deferred', 'scheduled'}:
                    active_rq_jobs.add(job.id)
                    continue
                existing.delete()
                job.dispatched_at = None
            except NoSuchJobError:
                job.dispatched_at = None

        available_slots = parallel_dispatch_capacity(db, len(active_rq_jobs))

        for job in jobs:
            if available_slots <= 0:
                break
            if job.id in active_rq_jobs or not provider_retry_ready(job):
                continue
            worker_target = 'app.day2.worker.execute' if job.operation.startswith('day2.') else 'app.jobs.worker.execute'
            q.enqueue(worker_target, job.id, job_id=job.id,
                      job_timeout=settings().execution_timeout + 120, result_ttl=86400, failure_ttl=86400)
            job.dispatched_at = now()
            available_slots -= 1
        reconcile_cancelled_jobs(db)
        reconcile_deployment_job_statuses(db)
        reconcile_persisted_inventory(db)
        reconcile_stale_jobs(db)
        from app.day2.reconciliation import reconcile_finished_jobs
        reconcile_finished_jobs(db)
        db.commit()
    reconcile_proxmox_tasks_once()
    queue_system_alert_webhooks_once()
    dispatch_event_broker_once()
    deliver_webhooks_once()
    cleanup_retention_once()
    from app.instance_backup.service import cleanup_expired_backups
    cleanup_expired_backups()
    redis_client().set('cp:dispatcher:heartbeat', 'alive', ex=30)


def dispatch_once():
    with normal_instance_operation():
        return _dispatch_once_unfenced()


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
