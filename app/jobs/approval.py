from datetime import timedelta

from app.blueprint_settings import blueprint_execution_settings
from app.models import JobLog, now


def blueprint_snapshot(job, deployment=None):
    payload = dict((job.payload or {}).get('blueprint') or {})
    if payload:
        return payload
    if deployment is None:
        return {}
    return dict(((deployment.workflow or {}).get('blueprint') or {}))


def blueprint_requires_approval(job, deployment=None):
    return job.operation == 'terraform.apply' and bool(
        blueprint_snapshot(job, deployment).get('requires_approval')
    )


def gate_job_for_approval(db, job, deployment=None):
    """Apply the current global approval policy to every Blueprint terraform.apply job."""
    blueprint = blueprint_snapshot(job, deployment)
    if job.operation != 'terraform.apply' or not blueprint.get('requires_approval'):
        return False

    config = blueprint_execution_settings(db)
    payload = dict(job.payload or {})
    if config['auto_approve_for_executors']:
        payload['_approval'] = {
            'status': 'approved',
            'approved_by': job.created_by,
            'approved_at': now().isoformat(),
            'automatic': True,
        }
        job.payload = payload
        db.add(JobLog(
            job_id=job.id,
            message='workflow.approval.auto: blueprints.execute uprawnia do automatycznej akceptacji',
        ))
        return True

    payload['_approval'] = {'status': 'pending'}
    has_runtime_approval = any(
        str(step.get('type')) == 'approval'
        for step in (blueprint.get('steps') or [])
    )
    if has_runtime_approval:
        db.add(JobLog(
            job_id=job.id,
            message='workflow.approval.deferred: approval nastąpi w zadanym kroku DAG',
        ))
    else:
        expires_at = now() + timedelta(hours=config['approval_timeout_hours'])
        payload['_approval'].update({
            'requested_at': now().isoformat(),
            'expires_at': expires_at.isoformat(),
        })
        job.status = 'waiting_approval'
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'waiting_approval'
        db.add(JobLog(
            job_id=job.id,
            message=(
                'workflow.approval.pending: oczekiwanie na zatwierdzenie; '
                f'expires_at={expires_at.isoformat()}'
            ),
        ))
    job.payload = payload
    return True
