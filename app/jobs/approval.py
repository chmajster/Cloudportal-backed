from app.blueprint_settings import blueprint_execution_settings
from app.models import JobLog, now


def blueprint_requires_approval(job, deployment=None):
    if job.operation != 'terraform.apply':
        return False
    payload_blueprint = ((job.payload or {}).get('blueprint') or {})
    deployment_blueprint = (((deployment.workflow or {}).get('blueprint') or {}) if deployment else {})
    blueprint = payload_blueprint or deployment_blueprint
    return bool(blueprint.get('requires_approval'))


def gate_job_for_approval(db, job, deployment=None):
    """Apply one fresh approval decision to every Blueprint terraform.apply job."""
    if not blueprint_requires_approval(job, deployment):
        return False

    payload = dict(job.payload or {})
    if blueprint_execution_settings(db)['auto_approve_for_executors']:
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
    job.payload = payload
    job.status = 'waiting_approval'
    if deployment is not None and deployment.active_job_id == job.id:
        deployment.status = 'waiting_approval'
    db.add(JobLog(
        job_id=job.id,
        message='workflow.approval.pending: oczekiwanie na zatwierdzenie',
    ))
    return True
