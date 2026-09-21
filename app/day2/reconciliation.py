"""Mirror dispatcher outcomes without releasing uncertain provider executions."""
from sqlalchemy import select
from app.day2.models import Day2ActionRequest
from app.day2.service import release_resource_lock
from app.models import Job, now


def reconcile_finished_jobs(db):
    jobs = db.scalars(
        select(Job).join(Day2ActionRequest, Day2ActionRequest.job_id == Job.id)
        .where(Job.operation.like('day2.%'), Job.status.in_(['failed', 'cancelled']),
               Day2ActionRequest.status.in_(['QUEUED', 'WAITING_APPROVAL', 'RUNNING', 'CANCEL_REQUESTED']))
        .order_by(Job.id).with_for_update(of=Job, skip_locked=True).limit(200)
    ).all()
    for job in jobs:
        row = db.scalar(select(Day2ActionRequest).where(Day2ActionRequest.job_id == job.id)
                        .with_for_update())
        if row is None or row.status not in {'QUEUED', 'WAITING_APPROVAL', 'RUNNING', 'CANCEL_REQUESTED'}:
            continue
        uncertain = row.status in {'RUNNING', 'CANCEL_REQUESTED'}
        row.status = 'CANCELLED' if job.status == 'cancelled' else 'FAILED'
        row.finished_at = now()
        row.error_code = 'RECONCILIATION_REQUIRED' if uncertain else 'JOB_TERMINATED'
        row.error_message = job.error or 'Job terminated by dispatcher'
        row.result = {**(row.result or {}), 'reconciliation_required': uncertain}
        if row.approval_state == 'pending':
            row.approval_state = 'cancelled'
        if not uncertain:
            release_resource_lock(db, row.resource_id, row.id)
