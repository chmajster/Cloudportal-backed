import time
from datetime import datetime, timedelta
from functools import lru_cache
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.job import Job as RQJob
from rq.exceptions import NoSuchJobError
from rq.serializers import JSONSerializer
from sqlalchemy import select
from app.config import settings
from app.database import session
from app.models import Deployment, Job, JobLog, now
from app.operations.service import cleanup_retention_once, deliver_webhooks_once, materialize_scheduled_jobs, queue_job_webhooks, queue_system_alert_webhooks_once
from app.security.core import redis_client


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


def reconcile_deployment_job_statuses(db):
    deployments = db.scalars(
        select(Deployment).where(Deployment.active_job_id.is_not(None)).with_for_update(skip_locked=True).limit(200)
    ).all()
    for deployment in deployments:
        job = db.get(Job, deployment.active_job_id)
        if job is None:
            deployment.active_job_id = None
            deployment.status = 'failed'
            db.add(JobLog(job_id=deployment.id, message='deployment.reconcile: active job record missing'))
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
        if job.status != 'successful':
            deployment.status = job.status
        elif job.operation == 'terraform.destroy':
            deployment.status = 'destroyed'
            if deployment.destroyed_at is None:
                deployment.destroyed_at = now()
        elif job.operation == 'terraform.import':
            deployment.status = 'imported'
        elif job.operation == 'terraform.plan':
            deployment.status = (job.payload or {}).get('previous_status', deployment.status or 'failed')
        else:
            deployment.status = 'successful'


def dispatch_once():
    materialize_scheduled_jobs()
    q = queue()
    with session() as db:
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
        reconcile_deployment_job_statuses(db)
        # Fail uncertain interrupted executions instead of blindly applying again.
        stale = db.scalars(select(Job).where(Job.status == 'running', Job.heartbeat_at < now() - timedelta(seconds=settings().execution_timeout + 180))
                           .with_for_update(skip_locked=True)).all()
        for job in stale:
            job.status = 'failed'
            job.error = 'Worker interrupted. Inspect Terraform state before explicitly retrying.'
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
