"""Dispatcher batches must not be consumed by already reconciled history."""
import uuid
from sqlalchemy import select
from app.database import session
from app.day2.models import Day2ActionRequest
from app.day2.reconciliation import reconcile_finished_jobs
from app.models import Job, Token, User


def test_terminal_history_does_not_starve_pending_reconciliation(system):
    with session() as db:
        owner = db.scalar(select(User).where(User.username == 'admin'))
        token = db.scalar(select(Token).where(Token.user_id == owner.id))
        for index in range(201):
            job = Job(id=f'{index:036d}', operation='day2.power_on', status='failed',
                      created_by=owner.id, token_id=token.id, request_id=str(uuid.uuid4()))
            db.add(job); db.flush()
            row = Day2ActionRequest(resource_id=str(uuid.uuid4()), action='power_on',
                                    requested_by=owner.id, job_id=job.id,
                                    status='QUEUED' if index == 200 else 'FAILED',
                                    request_id=str(uuid.uuid4()))
            db.add(row); db.flush()
            if index == 200:
                pending_id = row.id
        db.commit()
    with session() as db:
        reconcile_finished_jobs(db); db.commit()
    with session() as db:
        pending = db.get(Day2ActionRequest, pending_id)
        assert pending.status == 'FAILED'
        assert pending.error_code == 'JOB_TERMINATED'
        assert pending.result['reconciliation_required'] is False
