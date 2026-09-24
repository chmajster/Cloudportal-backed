import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, exists, func, select

from app.config import settings
from app.catalog import snapshot_ansible_payload
from app.database import session
from app.jobs.approval import gate_job_for_approval
from app.jobs.lifecycle import has_released_allocations
from app.quotas.service import prepare_job_reservation
from app.models import (
    Audit,
    Credential,
    HostnameReservation,
    Idempotency,
    IPAllocation,
    Deployment,
    EventConsumer,
    EventRecord,
    ExtensionDelivery,
    ExtensionState,
    Job,
    JobLog,
    ScheduledOperation,
    Setting,
    WebhookDelivery,
    WebhookEndpoint,
    now,
)
from app.security.core import decrypt_blob, effective_permissions, encrypt_blob, redis_client


def required_operation_permissions(operation, deployment=None):
    permissions = {'jobs.execute', 'terraform.execute'}
    if operation == 'terraform.apply':
        permissions.add('deployments.create')
        if deployment and ((deployment.workflow or {}).get('ansible') or (deployment.workflow or {}).get('ansible_runs')):
            permissions.add('ansible.execute')
        blueprint = (deployment.workflow or {}).get('blueprint', {}) if deployment else {}
        if blueprint:
            permissions.add('blueprints.execute')
        if blueprint.get('recovery_policy') == 'destroy_on_failure':
            permissions.add('deployments.destroy')
        workflow_types = {str(step.get('type')) for step in (blueprint.get('steps') or [])}
        if 'run_ansible_playbook' in workflow_types:
            permissions.add('ansible.execute')
        if 'create_snapshot' in workflow_types:
            permissions.add('snapshots.create')
        if 'terraform_destroy' in workflow_types:
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
            deployment = db.scalar(select(Deployment).where(
                Deployment.id == schedule.deployment_id
            ).with_for_update())
            if deployment is None:
                schedule.is_active = False
                schedule.last_error = 'Deployment no longer exists'
                continue

            if deployment.status == 'destroyed':
                schedule.is_active = False
                schedule.last_error = 'Deployment is already destroyed'
                continue

            if schedule.operation == 'terraform.apply' and has_released_allocations(db, deployment.id):
                schedule.is_active = False
                schedule.last_error = 'Deployment allocations were released; execute the Blueprint again'
                continue

            if (
                schedule.operation == 'terraform.apply'
                and ((deployment.workflow or {}).get('adoption') or {}).get('plan_only')
            ):
                schedule.is_active = False
                schedule.last_error = 'Adopted deployment is plan-only; terraform.apply is disabled'
                continue

            if deployment.active_job_id:
                schedule.last_error = 'Deployment is busy; scheduled operation postponed'
                schedule.next_run_at = current_time + timedelta(seconds=60)
                continue

            permissions = scheduler_user_permissions(db, schedule.created_by)
            if permissions is None:
                schedule.is_active = False
                schedule.last_error = 'Schedule owner is disabled or locked'
                continue
            try:
                from app.resource_scope.authorization import Scope, permissions_for_identity, ensure_execution_ready
                from app.tenancy.authorization import Identity
                scope = Scope(schedule.tenant_id, schedule.project_id)
                if (deployment.tenant_id, deployment.project_id) != (scope.tenant_id, scope.project_id):
                    raise HTTPException(409, 'Schedule and deployment scope do not match')
                identity = Identity(schedule.created_by, 0, frozenset(permissions), None)
                scoped = permissions_for_identity(db, identity, scope, write=True)
                ensure_execution_ready(db, identity, scope)
                ensure_operation_permissions(scoped, schedule.operation, deployment)
            except HTTPException:
                schedule.is_active = False
                schedule.last_error = 'Schedule owner no longer has required execution permissions'
                continue

            payload = dict(deployment.workflow if schedule.operation == 'terraform.apply' else {})
            try:
                payload = snapshot_ansible_payload(db, payload)
            except HTTPException as error:
                schedule.is_active = False
                schedule.last_error = ('Scheduled Ansible playbook is unavailable: ' + str(error.detail))[:500]
                continue

            job = Job(
                id=str(uuid.uuid4()),
                operation=schedule.operation,
                deployment_id=deployment.id,
                payload=payload,
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
            gate_job_for_approval(db, job, deployment)
            if job.status == 'queued':
                try:
                    with db.begin_nested():
                        prepare_job_reservation(db, job, deployment)
                except HTTPException as error:
                    job.status = 'failed'
                    job.error = 'Scheduled operation rejected by quota policy'
                    deployment.active_job_id = None
                    deployment.status = job.payload.get('previous_status', deployment.status)
                    schedule.last_error = (
                        error.detail.get('message', 'Quota policy rejected the scheduled operation')
                        if isinstance(error.detail, dict) else 'Quota policy rejected the scheduled operation'
                    )
                    schedule.last_run_at = current_time
                    if schedule.interval_seconds:
                        next_run = schedule.next_run_at
                        while next_run <= current_time:
                            next_run += timedelta(seconds=schedule.interval_seconds)
                        schedule.next_run_at = next_run
                    else:
                        schedule.is_active = False
                    continue
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


def queue_webhook_event(db, event, resource_id, data):
    # Backward-compatible entry point used across jobs/recovery/system alerts.
    # The event is now persisted transactionally; webhook fan-out happens through
    # the core.webhook-bridge extension in the dispatcher.
    from app.events.service import publish_event

    subject_type = event.split('.', 1)[0]
    request_id = None
    for key in ('job', 'recovery'):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and value.get('request_id'):
            request_id = str(value['request_id'])
            break
    return publish_event(
        db,
        event,
        data,
        subject_type=subject_type,
        subject_id=str(resource_id),
        request_id=request_id,
    )


def queue_job_webhooks(db, job):
    event = 'job.' + job.status
    if event in {'job.successful', 'job.failed', 'job.cancelled'}:
        queue_webhook_event(db, event, job.id, {
            'job': {
                'id': job.id,
                'deployment_id': job.deployment_id,
                'operation': job.operation,
                'status': job.status,
                'request_id': job.request_id,
                'attempt': job.attempt,
                'error': job.error,
            }
        })
    if job.source == 'Recovery' and job.status in {'successful', 'failed'}:
        queue_webhook_event(db, 'recovery.' + job.status, job.id, {
            'recovery': {
                'job_id': job.id,
                'deployment_id': job.deployment_id,
                'status': job.status,
                'recovery_of': (job.payload or {}).get('recovery_of'),
                'error': job.error,
            }
        })


def queue_system_alert_webhooks_once():
    try:
        if not redis_client().set('cp:system-alerts:scan', '1', nx=True, ex=60):
            return
    except Exception:
        return

    current = now()
    horizon = current + timedelta(days=settings().credential_expiry_warning_days)
    alerts = []
    with session() as db:
        stuck = db.scalars(select(Job).where(
            Job.status == 'running',
            Job.heartbeat_at.is_not(None),
            Job.heartbeat_at < current - timedelta(seconds=settings().execution_timeout + 180),
        ).limit(100)).all()
        for job in stuck:
            alerts.append({
                'severity': 'critical',
                'code': 'job_stuck',
                'resource_id': job.id,
                'message': 'Job heartbeat is stale',
            })

        credentials = db.scalars(select(Credential).where(
            (Credential.expires_at.is_not(None) & (Credential.expires_at <= horizon))
            | (Credential.rotation_due_at.is_not(None) & (Credential.rotation_due_at <= horizon))
        ).limit(100)).all()
        for credential in credentials:
            if credential.expires_at is not None and credential.expires_at <= current:
                alerts.append({
                    'severity': 'critical', 'code': 'credential_expired',
                    'resource_id': str(credential.id), 'message': 'Credential is expired',
                })
            elif credential.expires_at is not None and credential.expires_at <= horizon:
                alerts.append({
                    'severity': 'warning', 'code': 'credential_expiring',
                    'resource_id': str(credential.id), 'message': 'Credential expires soon',
                })
            if credential.rotation_due_at is not None and credential.rotation_due_at <= horizon:
                alerts.append({
                    'severity': 'warning', 'code': 'credential_rotation_due',
                    'resource_id': str(credential.id), 'message': 'Credential rotation is due soon',
                })

        failed_delivery = db.scalar(select(WebhookDelivery.id).where(
            WebhookDelivery.status == 'failed',
            WebhookDelivery.event != 'system.alert',
        ).limit(1))
        if failed_delivery:
            alerts.append({
                'severity': 'warning', 'code': 'webhook_delivery_failed',
                'resource_id': str(failed_delivery), 'message': 'Webhook delivery exhausted retries',
            })

        for alert in alerts:
            fingerprint = hashlib.sha256(json.dumps(alert, sort_keys=True).encode()).hexdigest()
            try:
                first = redis_client().set('cp:system-alert:' + fingerprint, '1', nx=True, ex=3600)
            except Exception:
                first = False
            if first:
                queue_webhook_event(db, 'system.alert', alert['resource_id'], {'alert': alert})
        db.commit()


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
        'events': current - timedelta(days=settings().retention_events_days),
        'idempotency': current - timedelta(days=settings().retention_idempotency_days),
        'released': current - timedelta(days=settings().retention_released_allocations_days),
    }
    counts = {}
    with session() as db:
        from app.events.service import sync_extension_states
        sync_extension_states(db)

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

        consumer_cursor = db.scalar(
            select(EventConsumer.cursor_sequence)
            .where(EventConsumer.is_active.is_(True))
            .order_by(EventConsumer.cursor_sequence.asc())
            .limit(1)
        )
        extension_cursor = db.scalar(
            select(ExtensionState.last_event_sequence)
            .where(ExtensionState.is_enabled.is_(True))
            .order_by(ExtensionState.last_event_sequence.asc())
            .limit(1)
        )
        cursor_candidates = [
            value for value in (consumer_cursor, extension_cursor) if value is not None
        ]
        safe_sequence = min(cursor_candidates) if cursor_candidates else None

        protected_delivery = exists(
            select(ExtensionDelivery.id).where(
                ExtensionDelivery.event_sequence == EventRecord.sequence,
                ExtensionDelivery.status.in_(['pending', 'dead_letter']),
            )
        )
        event_conditions = [
            EventRecord.created_at < cutoffs['events'],
            ~protected_delivery,
        ]
        if safe_sequence is not None:
            event_conditions.append(EventRecord.sequence <= safe_sequence)

        highest_deleted_sequence = db.scalar(
            select(func.max(EventRecord.sequence)).where(*event_conditions)
        )
        counts['events'] = db.execute(
            delete(EventRecord).where(*event_conditions)
        ).rowcount or 0

        retention_state = db.scalar(
            select(Setting).where(Setting.key == 'event_retention').with_for_update()
        )
        if retention_state is None:
            retention_state = Setting(key='event_retention', value={'floor_sequence': 0})
            db.add(retention_state)
            db.flush()
        if highest_deleted_sequence is not None:
            value = dict(retention_state.value or {})
            try:
                current_floor = int(value.get('floor_sequence', 0))
            except (TypeError, ValueError):
                current_floor = 0
            value['floor_sequence'] = max(current_floor, int(highest_deleted_sequence))
            retention_state.value = value
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
