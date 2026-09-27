import uuid

from sqlalchemy import select

from app.models import Audit, Blueprint, Job, JobLog


BLUEPRINT_DELETE_OPERATION = 'blueprint.delete'
ACTIVE_BLUEPRINT_JOB_STATUSES = frozenset({
    'waiting_approval', 'queued', 'running', 'cancelling',
})
PROVISIONING_OPERATIONS = frozenset({'terraform.apply', 'proxmox.provision'})


class BlueprintDeletionDeferred(RuntimeError):
    def __init__(self, blocking_job_ids: list[str]):
        self.blocking_job_ids = list(blocking_job_ids)
        super().__init__(
            'Blueprint deletion is waiting for active provisioning jobs: '
            + ', '.join(self.blocking_job_ids)
        )


def _payload_blueprint_id(job: Job) -> int | None:
    payload = job.payload or {}
    raw = payload.get('blueprint_id')
    if raw is None:
        raw = (payload.get('blueprint') or {}).get('id')
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def active_blueprint_provisioning_jobs(db, blueprint_id: int, *, exclude_job_id: str | None = None) -> list[Job]:
    rows = db.scalars(
        select(Job).where(
            Job.operation.in_(PROVISIONING_OPERATIONS),
            Job.status.in_(ACTIVE_BLUEPRINT_JOB_STATUSES),
        ).order_by(Job.created_at.asc())
    ).all()
    expected = int(blueprint_id)
    return [
        job for job in rows
        if job.id != exclude_job_id and _payload_blueprint_id(job) == expected
    ]


def pending_blueprint_delete_job(db, blueprint_id: int) -> Job | None:
    rows = db.scalars(
        select(Job).where(
            Job.operation == BLUEPRINT_DELETE_OPERATION,
            Job.status.in_(ACTIVE_BLUEPRINT_JOB_STATUSES),
        ).order_by(Job.created_at.asc())
    ).all()
    expected = int(blueprint_id)
    return next((job for job in rows if _payload_blueprint_id(job) == expected), None)


def queue_blueprint_delete(
    db,
    blueprint: Blueprint,
    *,
    actor_id: int,
    token_id: int | None,
    request_id: str,
    ip: str,
    source: str,
    blocking_jobs: list[Job],
) -> Job:
    existing = pending_blueprint_delete_job(db, blueprint.id)
    if existing is not None:
        return existing

    blocking_job_ids = [job.id for job in blocking_jobs]
    job = Job(
        id=str(uuid.uuid4()),
        operation=BLUEPRINT_DELETE_OPERATION,
        payload={
            'blueprint_id': blueprint.id,
            'blueprint_name': blueprint.name,
            'blocking_job_ids': blocking_job_ids,
            '_current_stage': 'blueprint.delete.waiting_for_provisioning',
        },
        created_by=actor_id,
        token_id=token_id,
        request_id=request_id,
        ip=ip,
        source=source,
    )
    db.add(job)
    db.flush()
    db.add(JobLog(
        job_id=job.id,
        message=(
            'blueprint.delete.queued: waiting for provisioning jobs '
            + ', '.join(blocking_job_ids)
        ),
    ))
    return job


def blueprint_delete_ready(db, job: Job) -> bool:
    if job.operation != BLUEPRINT_DELETE_OPERATION:
        return True
    blueprint_id = _payload_blueprint_id(job)
    if blueprint_id is None:
        return True
    return not active_blueprint_provisioning_jobs(
        db,
        blueprint_id,
        exclude_job_id=job.id,
    )


def perform_blueprint_delete(db, job: Job) -> bool:
    blueprint_id = _payload_blueprint_id(job)
    if blueprint_id is None:
        raise ValueError('Blueprint delete job has no valid blueprint_id')

    row = db.scalar(
        select(Blueprint)
        .where(Blueprint.id == blueprint_id)
        .with_for_update()
    )
    if row is None:
        db.add(JobLog(
            job_id=job.id,
            message=f'blueprint.delete.noop: Blueprint {blueprint_id} is already absent',
        ))
        return False

    if (
        str(row.tenant_id) != str(job.tenant_id)
        or str(row.project_id) != str(job.project_id)
    ):
        raise ValueError('Blueprint delete job scope does not match Blueprint scope')

    blocking_jobs = active_blueprint_provisioning_jobs(
        db,
        blueprint_id,
        exclude_job_id=job.id,
    )
    if blocking_jobs:
        raise BlueprintDeletionDeferred([item.id for item in blocking_jobs])

    db.delete(row)
    db.add(JobLog(
        job_id=job.id,
        message=f'blueprint.delete.completed: Blueprint {blueprint_id} deleted',
    ))
    db.add(Audit(
        user_id=job.created_by,
        token_id=job.token_id,
        ip=job.ip,
        source=job.source,
        action='blueprint.deleted',
        resource='blueprints',
        resource_id=str(blueprint_id),
        result='success',
        request_id=job.request_id,
    ))
    return True
