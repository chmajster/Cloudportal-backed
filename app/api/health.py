import shutil
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from rq import Queue, Worker
from sqlalchemy import func, select, text

from app.api.outputs import HealthOutput, InfoOutput
from app.catalog import list_templates
from app.config import settings
from app.database import session
from app.models import (
    Credential,
    Deployment,
    IPAllocation,
    Job,
    ManagedResource,
    ScheduledOperation,
    WebhookDelivery,
    now,
)
from app.security.core import encryption_key, redis_client, require
from app.version import build_commit, build_version

router = APIRouter(tags=['health'])


def health_status():
    checks = {
        'api': True,
        'database': False,
        'queue': False,
        'dispatcher': False,
        'workers': {'online': 0, 'expected': settings().worker_count},
        'terraform': bool(shutil.which('terraform')),
        'ansible': bool(shutil.which('ansible-playbook')),
        'disk': False,
        'encryption': False,
    }
    try:
        with session() as db:
            db.execute(text('SELECT 1'))
            db.execute(text('SELECT version_num FROM alembic_version'))
        checks['database'] = True
    except Exception:
        pass
    try:
        checks['queue'] = bool(redis_client().ping())
        checks['dispatcher'] = bool(redis_client().get('cp:dispatcher:heartbeat'))
        workers = Worker.all(connection=redis_client())
        checks['workers']['online'] = sum(
            1
            for worker in workers
            if 'cloudportal' in worker.queue_names()
            and worker.last_heartbeat
            and worker.last_heartbeat.replace(tzinfo=timezone.utc)
            > datetime.now(timezone.utc) - timedelta(seconds=120)
        )
    except Exception:
        pass
    try:
        checks['disk'] = shutil.disk_usage(settings().data_dir).free > 1024 ** 3
        encryption_key()
        checks['encryption'] = True
    except Exception:
        pass
    ready = (
        all(value for key, value in checks.items() if key != 'workers')
        and checks['workers']['online'] >= checks['workers']['expected']
    )
    return {'status': 'ok' if ready else 'degraded', 'checks': checks}


@router.get(
    '/health',
    response_model=HealthOutput,
    responses={503: {'model': HealthOutput, 'description': 'A required component is unavailable'}},
)
def health():
    status = health_status()
    return JSONResponse(status, status_code=200 if status['status'] == 'ok' else 503)


@router.get('/info', response_model=InfoOutput)
def info(actor=Depends(require('portal.connect'))):
    return {
        'name': 'Cloudportal-backed',
        'version': build_version(),
        'api_version': 'v1',
        'providers': sorted({item['provider'] for item in list_templates()}),
        'credential_types': ['proxmox', 'vmware', 'ssh', 'winrm', 'aws', 'azure', 'openstack', 'other'],
        'executors': ['terraform', 'ansible', 'opentofu'],
        'openapi': '/openapi.json',
    }


def _escape_label(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _metric(lines, name, value, labels=None):
    suffix = ''
    if labels:
        suffix = '{' + ','.join(
            f'{key}="{_escape_label(val)}"' for key, val in sorted(labels.items())
        ) + '}'
    lines.append(f'{name}{suffix} {value}')


def _group_counts(db, model, *columns):
    rows = db.execute(
        select(*columns, func.count()).select_from(model).group_by(*columns)
    ).all()
    return rows


def prometheus_metrics():
    lines = [
        '# HELP cloudportal_build_info Cloudportal-backed build information.',
        '# TYPE cloudportal_build_info gauge',
    ]
    _metric(lines, 'cloudportal_build_info', 1, {'version': build_version(), 'commit': build_commit()})

    status = health_status()
    _metric(lines, 'cloudportal_workers_online', status['checks']['workers']['online'])
    _metric(lines, 'cloudportal_workers_expected', status['checks']['workers']['expected'])
    _metric(lines, 'cloudportal_dispatcher_up', int(status['checks']['dispatcher']))
    _metric(lines, 'cloudportal_database_up', int(status['checks']['database']))
    _metric(lines, 'cloudportal_redis_up', int(status['checks']['queue']))

    try:
        queue_depth = Queue('cloudportal', connection=redis_client()).count
    except Exception:
        queue_depth = -1
    _metric(lines, 'cloudportal_queue_depth', queue_depth)

    with session() as db:
        for operation, job_status, count in _group_counts(db, Job, Job.operation, Job.status):
            _metric(
                lines,
                'cloudportal_jobs_total',
                count,
                {'operation': operation, 'status': job_status},
            )
        stale_threshold = now() - timedelta(seconds=settings().execution_timeout + 180)
        stuck_jobs = db.scalar(
            select(func.count()).select_from(Job).where(
                Job.status == 'running',
                Job.heartbeat_at.is_not(None),
                Job.heartbeat_at < stale_threshold,
            )
        ) or 0
        _metric(lines, 'cloudportal_jobs_stuck', stuck_jobs)

        for deployment_status, count in _group_counts(db, Deployment, Deployment.status):
            _metric(
                lines,
                'cloudportal_deployments_total',
                count,
                {'status': deployment_status},
            )

        for provider, lifecycle_status, count in _group_counts(
            db, ManagedResource, ManagedResource.provider, ManagedResource.lifecycle_status
        ):
            _metric(
                lines,
                'cloudportal_managed_resources_total',
                count,
                {'provider': provider, 'status': lifecycle_status},
            )

        for allocation_status, count in _group_counts(db, IPAllocation, IPAllocation.status):
            _metric(
                lines,
                'cloudportal_ip_allocations_total',
                count,
                {'status': allocation_status},
            )

        schedules_active = db.scalar(
            select(func.count()).select_from(ScheduledOperation).where(
                ScheduledOperation.is_active.is_(True)
            )
        ) or 0
        _metric(lines, 'cloudportal_schedules_active', schedules_active)

        for delivery_status, count in _group_counts(
            db, WebhookDelivery, WebhookDelivery.status
        ):
            _metric(
                lines,
                'cloudportal_webhook_deliveries_total',
                count,
                {'status': delivery_status},
            )

    return '\n'.join(lines) + '\n'


@router.get('/metrics', response_class=PlainTextResponse)
def metrics(actor=Depends(require('metrics.read'))):
    return PlainTextResponse(
        prometheus_metrics(),
        media_type='text/plain; version=0.0.4; charset=utf-8',
    )


@router.get('/alerts')
def alerts(actor=Depends(require('metrics.read'))):
    health = health_status()
    result = []
    if not health['checks']['database']:
        result.append({'severity': 'critical', 'code': 'database_down', 'message': 'PostgreSQL is unavailable'})
    if not health['checks']['queue']:
        result.append({'severity': 'critical', 'code': 'redis_down', 'message': 'Redis is unavailable'})
    if not health['checks']['dispatcher']:
        result.append({'severity': 'critical', 'code': 'dispatcher_down', 'message': 'Dispatcher heartbeat is missing'})
    online = health['checks']['workers']['online']
    expected = health['checks']['workers']['expected']
    if online < expected:
        result.append({
            'severity': 'critical',
            'code': 'workers_missing',
            'message': f'Only {online} of {expected} expected workers are online',
        })
    if not health['checks']['disk']:
        result.append({'severity': 'warning', 'code': 'disk_low', 'message': 'Less than 1 GiB free in the data directory'})

    with session() as db:
        threshold = now() - timedelta(seconds=settings().execution_timeout + 180)
        stuck = db.scalars(
            select(Job).where(
                Job.status == 'running',
                Job.heartbeat_at.is_not(None),
                Job.heartbeat_at < threshold,
            ).limit(100)
        ).all()
        for job in stuck:
            result.append({
                'severity': 'critical',
                'code': 'job_stuck',
                'resource_id': job.id,
                'message': f'Job {job.id} has a stale heartbeat',
            })
        warning_horizon = now() + timedelta(days=settings().credential_expiry_warning_days)
        credentials = db.scalars(select(Credential).where(
            (Credential.expires_at.is_not(None) & (Credential.expires_at <= warning_horizon))
            | (Credential.rotation_due_at.is_not(None) & (Credential.rotation_due_at <= warning_horizon))
        ).limit(100)).all()
        for credential in credentials:
            if credential.expires_at is not None and credential.expires_at <= now():
                result.append({
                    'severity': 'critical',
                    'code': 'credential_expired',
                    'resource_id': str(credential.id),
                    'message': f'Credential {credential.name} is expired',
                })
            elif credential.expires_at is not None and credential.expires_at <= warning_horizon:
                result.append({
                    'severity': 'warning',
                    'code': 'credential_expiring',
                    'resource_id': str(credential.id),
                    'message': f'Credential {credential.name} expires soon',
                })
            if credential.rotation_due_at is not None and credential.rotation_due_at <= warning_horizon:
                result.append({
                    'severity': 'warning',
                    'code': 'credential_rotation_due',
                    'resource_id': str(credential.id),
                    'message': f'Credential {credential.name} rotation is due soon',
                })

        failed_webhooks = db.scalar(
            select(func.count()).select_from(WebhookDelivery).where(
                WebhookDelivery.status == 'failed'
            )
        ) or 0
        if failed_webhooks:
            result.append({
                'severity': 'warning',
                'code': 'webhook_failures',
                'count': failed_webhooks,
                'message': f'{failed_webhooks} webhook deliveries exhausted retries',
            })
    return {'items': result}
