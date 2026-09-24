from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job as RQJob
from rq.serializers import JSONSerializer

from app.config import settings
from app.jobs.queue import queue
from app.models import JobLog, now
from app.security.core import redis_client


ACTIVE_RQ_STATUSES = {'queued', 'started', 'deferred', 'scheduled'}


class ForceDispatchConflict(RuntimeError):
    pass


class ForceDispatchUnavailable(RuntimeError):
    pass


def force_dispatch_job(db, job):
    """Enqueue one queued job without applying the global parallel-job limit."""
    if job.status != 'queued':
        raise ForceDispatchConflict('Only queued jobs can be force-dispatched')
    if job.cancel_requested:
        raise ForceDispatchConflict('Cancellation was already requested for this job')
    if (job.payload or {}).get('_provider_wait'):
        raise ForceDispatchConflict('Job is waiting for provider retry and cannot bypass provider backoff')

    # The API route holds a row lock on this Job. The normal dispatcher uses
    # SELECT ... FOR UPDATE SKIP LOCKED, so it skips a force-dispatched row
    # until this transaction commits. If the dispatcher won the row lock first,
    # the RQ lookup below detects the already-dispatched job.

    try:
        existing = RQJob.fetch(job.id, connection=redis_client(), serializer=JSONSerializer)
        rq_status = existing.get_status(refresh=True)
    except NoSuchJobError:
        existing = None
        rq_status = None
    except RedisError as exc:
        raise ForceDispatchUnavailable('Job queue is temporarily unavailable') from exc

    if rq_status in ACTIVE_RQ_STATUSES:
        raise ForceDispatchConflict('Job is already dispatched to the worker queue')

    if existing is not None:
        try:
            existing.delete()
        except RedisError as exc:
            raise ForceDispatchUnavailable('Job queue is temporarily unavailable') from exc

    worker_target = 'app.day2.worker.execute' if job.operation.startswith('day2.') else 'app.jobs.worker.execute'
    try:
        queue().enqueue(
            worker_target,
            job.id,
            job_id=job.id,
            job_timeout=settings().execution_timeout + 120,
            result_ttl=86400,
            failure_ttl=86400,
        )
    except RedisError as exc:
        raise ForceDispatchUnavailable('Job queue is temporarily unavailable') from exc

    job.dispatched_at = now()
    db.add(JobLog(
        job_id=job.id,
        message='job.force_dispatched: pominięto globalny limit równoległych zadań',
    ))
    return job
