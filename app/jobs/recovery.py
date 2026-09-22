"""Automatic recovery for Terraform jobs orphaned by a lost worker heartbeat."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.models import Audit, Deployment, Job, JobLog, ManagedResource, ManagedVM, now
from app.operations.service import queue_webhook_event
from app.quotas.service import job_reservation


def record_persisted_state_recovery(job: Job, item: dict) -> None:
    """Persist evidence that Terraform state recreated missing managed inventory."""
    if job.operation != 'terraform.apply' or not job.deployment_id:
        return
    payload = dict(job.payload or {})
    payload['_state_recovery'] = {
        'source_job_id': job.id,
        'deployment_id': job.deployment_id,
        'reconciled_at': now().isoformat(),
        'external_id': item.get('external_id'),
        'vm_id': item.get('vm_id'),
        'node': item.get('node'),
    }
    job.payload = payload


def _active_inventory_exists(db, deployment_id: str) -> bool:
    return bool(
        db.scalar(select(ManagedResource.id).where(
            ManagedResource.deployment_id == deployment_id,
            ManagedResource.lifecycle_status == 'active',
        ).limit(1))
        or db.scalar(select(ManagedVM.id).where(
            ManagedVM.deployment_id == deployment_id,
            ManagedVM.lifecycle_status == 'active',
        ).limit(1))
    )


def _safe_to_auto_retry(db, job: Job) -> bool:
    """Require job-specific state recovery plus reconciled accounting before retry."""
    payload = dict(job.payload or {})
    previous_auto = dict(payload.get('_auto_resume') or {})
    evidence = dict(payload.get('_state_recovery') or {})
    state_recovered_for_job = (
        evidence.get('source_job_id') == job.id
        and evidence.get('deployment_id') == job.deployment_id
    )
    if not state_recovered_for_job and previous_auto.get('from_persisted_state') is not True:
        return False
    if not _active_inventory_exists(db, job.deployment_id):
        return False

    reservation = job_reservation(db, job)
    if reservation is None:
        # A retry can be quota-neutral and therefore have no reservation.
        return True
    return (
        reservation.status == 'committed'
        and reservation.reconciliation_required is False
    )


def queue_automatic_resume(db, job: Job, deployment: Deployment | None) -> Job | None:
    """Queue a bounded retry once persisted state and quota are safe to reuse."""
    cfg = settings()
    if not cfg.worker_auto_resume_enabled or cfg.worker_auto_resume_max_attempts <= 0:
        return None
    if (
        job.operation != 'terraform.apply'
        or job.cancel_requested
        or not job.deployment_id
        or deployment is None
        or deployment.active_job_id != job.id
    ):
        return None
    if not _safe_to_auto_retry(db, job):
        return None

    payload = dict(job.payload or {})
    previous_auto = dict(payload.get('_auto_resume') or {})
    resume_count = int(previous_auto.get('count') or 0)
    if resume_count >= cfg.worker_auto_resume_max_attempts:
        return None

    # Retry gets fresh quota admission. For an initial create that was already
    # reconciled into quota accounting this is normally a zero delta. If desired
    # state changed meanwhile, normal quota admission protects the new delta.
    payload.pop('_provider_wait', None)
    payload.pop('_quota_checked', None)
    payload.pop('_quota_reservation_id', None)
    payload.pop('_state_recovery', None)
    runtime = dict(payload.get('_workflow_runtime') or {})
    runtime.pop('current_step', None)
    if runtime:
        payload['_workflow_runtime'] = runtime
    payload['_auto_resume'] = {
        'from_persisted_state': True,
        'count': resume_count + 1,
        'from_job_id': job.id,
        'queued_at': now().isoformat(),
        'reason': 'worker_heartbeat_lost',
    }

    resumed = Job(
        id=str(uuid.uuid4()),
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        deployment_id=job.deployment_id,
        operation=job.operation,
        payload=payload,
        status='queued',
        created_by=job.created_by,
        token_id=job.token_id,
        request_id=str(uuid.uuid4()),
        ip=job.ip,
        source='Recovery',
        retry_of=job.id,
        attempt=int(job.attempt or 1) + 1,
    )
    db.add(resumed)
    db.flush()

    deployment.active_job_id = resumed.id
    deployment.status = 'recovery_queued'
    db.add(JobLog(
        job_id=resumed.id,
        message=(
            f'recovery.auto_resume.queued: previous_job={job.id}; '
            f'attempt={resume_count + 1}/{cfg.worker_auto_resume_max_attempts}; '
            'terraform_apply=retry_with_restored_state'
        ),
    ))
    db.add(Audit(
        user_id=job.created_by,
        token_id=job.token_id,
        ip=job.ip,
        source='Recovery',
        action='recovery.queued',
        resource='jobs',
        resource_id=resumed.id,
        result='queued',
        request_id=resumed.request_id,
    ))
    queue_webhook_event(db, 'recovery.queued', resumed.id, {
        'recovery': {
            'job_id': resumed.id,
            'deployment_id': resumed.deployment_id,
            'status': 'queued',
            'recovery_of': job.id,
            'automatic': True,
            'from_persisted_state': True,
        }
    })
    return resumed
