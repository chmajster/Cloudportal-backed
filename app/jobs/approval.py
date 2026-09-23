from datetime import timedelta

from app.blueprint_settings import effective_blueprint_execution_settings
from app.models import JobLog, now


def blueprint_snapshot(job, deployment=None):
    payload = dict((job.payload or {}).get('blueprint') or {})
    if payload:
        return payload
    if deployment is None:
        return {}
    return dict(((deployment.workflow or {}).get('blueprint') or {}))


def blueprint_requires_approval(job, deployment=None):
    return job.operation == 'terraform.apply' and bool(blueprint_snapshot(job, deployment).get('requires_approval'))


def approval_policy_for_job(db, job, deployment=None):
    payload = dict(job.payload or {})
    persisted = payload.get('_approval_policy')
    if isinstance(persisted, dict):
        try:
            return {
                'auto_approve_for_executors': bool(persisted['auto_approve_for_executors']),
                'approval_timeout_hours': max(1, min(720, int(persisted['approval_timeout_hours']))),
                'auto_approve_source': str(persisted.get('auto_approve_source') or 'global'),
                'approval_timeout_source': str(persisted.get('approval_timeout_source') or 'global'),
            }
        except (KeyError, TypeError, ValueError):
            pass
    project_id = getattr(job, 'project_id', None) or getattr(deployment, 'project_id', None)
    return effective_blueprint_execution_settings(
        db, project_id=project_id, blueprint=blueprint_snapshot(job, deployment)
    )


def gate_job_for_approval(db, job, deployment=None):
    """Apply the effective Global -> Project -> Blueprint policy to Blueprint applies."""
    blueprint = blueprint_snapshot(job, deployment)
    if job.operation != 'terraform.apply' or not blueprint.get('requires_approval'):
        return False

    config = effective_blueprint_execution_settings(
        db,
        project_id=getattr(job, 'project_id', None) or getattr(deployment, 'project_id', None),
        blueprint=blueprint,
    )
    payload = dict(job.payload or {})
    payload['_approval_policy'] = {
        'auto_approve_for_executors': config['auto_approve_for_executors'],
        'approval_timeout_hours': config['approval_timeout_hours'],
        'auto_approve_source': config['auto_approve_source'],
        'approval_timeout_source': config['approval_timeout_source'],
    }
    if config['auto_approve_for_executors']:
        payload['_approval'] = {
            'status': 'approved', 'approved_by': job.created_by,
            'approved_at': now().isoformat(), 'automatic': True,
        }
        job.payload = payload
        db.add(JobLog(job_id=job.id, message=(
            'workflow.approval.auto: blueprints.execute uprawnia do automatycznej akceptacji; '
            f"policy_source={config['auto_approve_source']}"
        )))
        return True

    payload['_approval'] = {'status': 'pending'}
    has_runtime_approval = any(str(step.get('type')) == 'approval' for step in (blueprint.get('steps') or []))
    if has_runtime_approval:
        db.add(JobLog(job_id=job.id, message='workflow.approval.deferred: approval nastąpi w zadanym kroku DAG'))
    else:
        expires_at = now() + timedelta(hours=config['approval_timeout_hours'])
        payload['_approval'].update({'requested_at': now().isoformat(), 'expires_at': expires_at.isoformat()})
        job.status = 'waiting_approval'
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'waiting_approval'
        db.add(JobLog(job_id=job.id, message=(
            'workflow.approval.pending: oczekiwanie na zatwierdzenie; '
            f'expires_at={expires_at.isoformat()}; '
            f"policy_source={config['auto_approve_source']}; timeout_source={config['approval_timeout_source']}"
        )))
    job.payload = payload
    return True
