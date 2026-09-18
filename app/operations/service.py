import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.config import settings
from app.database import session
from app.models import (
    Audit,
    HostnameReservation,
    Idempotency,
    IPAllocation,
    Deployment,
    Job,
    JobLog,
    ScheduledOperation,
    WebhookDelivery,
    WebhookEndpoint,
    now,
)
from app.security.core import decrypt_blob, effective_permissions, encrypt_blob, redis_client


def required_operation_permissions(operation, deployment=None):
    permissions = {'jobs.execute', 'terraform.execute'}
    if operation == 'terraform.apply':
        permissions.add('deployments.create')
        if deployment and (deployment.workflow or {}).get('ansible'):
            permissions.add('ansible.execute')
        blueprint = (deployment.workflow or {}).get('blueprint', {}) if deployment else {}
        if blueprint.get('recovery_policy') == 'destroy_on_failure':
            permissions.add('deployments.destroy')
    if operation == 'terraform.destroy':
        permissions.add('deployments.destroy')
    return permissions


def ensure_operation_permissions(permissions, operation, deployment=None):
    required = required_operation_permissions(operation, deployment)
    if not required <= set(permissions):
        raise HTTPException(403, 'Missing permissions required by scheduled operation')


def validate_webhook_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    allowed = {item.lower().strip() for item in settings().webhook_allowed_hosts if item.strip()}
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(422, 'Webhook URL must be HTTPS without embedded credentials or fragment')
    if not allowed:
        raise HTTPException(503, 'Webhook destinations are disabled until CP_WEBHOOK_ALLOWED_HOSTS is configured')
    if host not in allowed:
        raise HTTPException(422, 'Webhook destination host is not allowlisted')
    return host


def new_webhook_secret(endpoint_id):
    plain = secrets.token_urlsafe(48)
    return plain, encrypt_blob(plain.encode(), f'webhook:{endpoint_id}')


def materialize_scheduled_jobs():
    current_time = now()
    with session() as db:
        rows = db.scalars(
            select(ScheduledOperation)
            .where(
                ScheduledOperation.is_active.is_(True),
                ScheduledOperation.next_run_at <= current_time,
            )
            .order_by(ScheduledOperation.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for schedule in rows:
            deployment = db.get(Deployment, schedule.deployment_id)
            if deployment is None:
                schedule.is_active = False
                schedule.last_error = 'Deployment no longer exists'
                continue

            if deployment.status == 'destroyed':
                schedule.is_active = False
                schedule.last_error = 'Deployment is already destroyed'
                continue

            if deployment.active_job_id:
                schedule.last_error = 'Deployment is busy; scheduled operation postponed'
                schedule.next_run_at = current_time + timedelta(seconds=60)
                continue

            job = Job(
                id=str(uuid.uuid4()),
                operation=schedule.operation,
                deployment_id=deployment.id,
                payload=dict(deployment.workflow if schedule.operation == 'terraform.apply' else {}),
                created_by=schedule.created_by,
                token_id=None,
                request_id=str(uuid.uuid4()),
                ip='',
                source='Scheduler',
            )
            job.payload['previous_status'] = deployment.status
            db.add(job)
            db.flush()
            deployment.active_job_id = job.id
            deployment.status = 'queued'
            schedule.last_run_at = current_time
            schedule.last_error = None
            if schedule.interval_seconds:
                next_run = schedule.next_run_at
                while next_run <= current_time:
                    next_run += timedelta(seconds=schedule.interval_seconds)
                schedule.next_run_at = next_run
            else:
                schedule.is_active = False
            db.add(Audit(
                user_id=schedule.created_by,
                token_id=None,
                ip='',
                source='Scheduler',
                action='schedule.triggered',
                resource='schedules',
                resource_id=schedule.id,
                request_id=job.request_id,
            ))
        db.commit()


def queue_job_webhooks(db, job):
    event = 'job.' + job.status
    if event not in {'job.successful', 'job.failed', 'job.cancelled'}:
        return
    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.is_active.is_(True))
    ).all()
    payload = {
        'event': event,
        'job': {
            'id': job.id,
            'deployment_id': job.deployment_id,
            'operation': job.operation,
            'status': job.status,
            'request_id': job.request_id,
            'attempt': job.attempt,
            'error': job.error,
        },
        'created_at': now().isoformat() + 'Z',
    }
    for endpoint in endpoints:
        if event in (endpoint.events or []):
            db.add(WebhookDelivery(
                endpoint_id=endpoint.id,
                event=event,
                resource_id=job.id,
                payload=payload,
            ))


def _delivery_signature(secret, body):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return 'sha256=' + digest


def deliver_webhooks_once():
    current_time = now()
    with session() as db:
        deliveries = db.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == 'pending',
                WebhookDelivery.next_attempt_at <= current_time,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .with_for_update(skip_locked=True)
            .limit(50)
        ).all()

        for delivery in deliveries:
            endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
            if endpoint is None or not endpoint.is_active:
                delivery.status = 'failed'
                delivery.last_error = 'Webhook endpoint is unavailable'
                continue
            try:
                validate_webhook_url(endpoint.url)
                secret = decrypt_blob(endpoint.encrypted_secret, f'webhook:{endpoint.id}').decode()
                body = json.dumps(
                    delivery.payload,
                    sort_keys=True,
                    separators=(',', ':'),
                ).encode()
                headers = {
                    'Content-Type': 'application/json',
                    'X-Cloudportal-Event': delivery.event,
                    'X-Cloudportal-Delivery': delivery.id,
                    'X-Cloudportal-Signature': _delivery_signature(secret, body),
                }
                with httpx.Client(
                    timeout=httpx.Timeout(10, connect=5),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.post(endpoint.url, content=body, headers=headers)
                    response.raise_for_status()
                delivery.status = 'delivered'
                delivery.delivered_at = now()
                delivery.last_error = None
            except Exception:
                delivery.attempts += 1
                if delivery.attempts >= 8:
                    delivery.status = 'failed'
                    delivery.last_error = 'Webhook delivery failed after maximum retries'
                else:
                    delay = min(3600, 60 * (2 ** max(0, delivery.attempts - 1)))
                    delivery.next_attempt_at = now() + timedelta(seconds=delay)
                    delivery.last_error = 'Webhook delivery failed; retry scheduled'
        db.commit()


def scheduler_user_permissions(db, user_id):
    from app.models import User
    user = db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.is_locked
        or (user.locked_until is not None and user.locked_until > now())
    ):
        return None
    return effective_permissions(user)



def cleanup_retention_once(force=False):
    if not force:
        try:
            if not redis_client().set('cp:retention:lock', '1', nx=True, ex=3600):
                return {'skipped': True}
        except Exception:
            return {'skipped': True}

    current = now()
    cutoffs = {
        'job_logs': current - timedelta(days=settings().retention_job_logs_days),
        'audit': current - timedelta(days=settings().retention_audit_days),
        'webhooks': current - timedelta(days=settings().retention_webhook_deliveries_days),
        'idempotency': current - timedelta(days=settings().retention_idempotency_days),
        'released': current - timedelta(days=settings().retention_released_allocations_days),
    }
    counts = {}
    with session() as db:
        counts['job_logs'] = db.execute(
            delete(JobLog).where(JobLog.timestamp < cutoffs['job_logs'])
        ).rowcount or 0
        counts['audit'] = db.execute(
            delete(Audit).where(Audit.timestamp < cutoffs['audit'])
        ).rowcount or 0
        counts['webhook_deliveries'] = db.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.status.in_(['delivered', 'failed']),
                WebhookDelivery.created_at < cutoffs['webhooks'],
            )
        ).rowcount or 0
        counts['idempotency'] = db.execute(
            delete(Idempotency).where(Idempotency.created_at < cutoffs['idempotency'])
        ).rowcount or 0
        counts['released_ip'] = db.execute(
            delete(IPAllocation).where(
                IPAllocation.status == 'released',
                IPAllocation.released_at.is_not(None),
                IPAllocation.released_at < cutoffs['released'],
            )
        ).rowcount or 0
        counts['released_hostnames'] = db.execute(
            delete(HostnameReservation).where(
                HostnameReservation.status == 'released',
                HostnameReservation.released_at.is_not(None),
                HostnameReservation.released_at < cutoffs['released'],
            )
        ).rowcount or 0
        db.commit()
    return {'skipped': False, 'deleted': counts}
