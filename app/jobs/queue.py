import time
from datetime import timedelta
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
from app.operations.service import deliver_webhooks_once, materialize_scheduled_jobs
from app.security.core import redis_client


@lru_cache
def queue_connection():
    # RQ blocks waiting for work for up to worker_ttl - 15 seconds. The API's
    # short Redis timeout must not interrupt this blocking dequeue operation.
    return Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=100)


def queue():
    return Queue('cloudportal', connection=queue_connection(), serializer=JSONSerializer)


def dispatch_once():
    materialize_scheduled_jobs()
    q = queue()
    with session() as db:
        jobs = db.scalars(select(Job).where(Job.status == 'queued', Job.cancel_requested.is_(False))
                          .with_for_update(skip_locked=True).limit(100)).all()
        for job in jobs:
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
        db.commit()
    deliver_webhooks_once()
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
