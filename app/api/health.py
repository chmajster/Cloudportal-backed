import shutil
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from rq import Worker
from sqlalchemy import text
from app.config import settings
from app.database import session
from app.security.core import encryption_key, redis_client, require

router = APIRouter(tags=['health'])


def health_status():
    checks = {'api': True, 'database': False, 'queue': False, 'workers': {'online': 0, 'expected': settings().worker_count},
              'terraform': bool(shutil.which('terraform')), 'ansible': bool(shutil.which('ansible-playbook')),
              'disk': False, 'encryption': False}
    try:
        with session() as db:
            db.execute(text('SELECT 1'))
            db.execute(text('SELECT version_num FROM alembic_version'))
        checks['database'] = True
    except Exception:
        pass
    try:
        checks['queue'] = bool(redis_client().ping())
        workers = Worker.all(connection=redis_client())
        from datetime import datetime, timezone, timedelta
        checks['workers']['online'] = sum(1 for w in workers if 'cloudportal' in w.queue_names()
                                         and w.last_heartbeat and w.last_heartbeat.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(seconds=120))
    except Exception:
        pass
    try:
        checks['disk'] = shutil.disk_usage(settings().data_dir).free > 1024 ** 3
        encryption_key()
        checks['encryption'] = True
    except Exception:
        pass
    ready = all(value for key, value in checks.items() if key != 'workers') and checks['workers']['online'] >= checks['workers']['expected']
    return {'status': 'ok' if ready else 'degraded', 'checks': checks}


@router.get('/health')
def health():
    status = health_status()
    return JSONResponse(status, status_code=200 if status['status'] == 'ok' else 503)


@router.get('/info')
def info(actor=Depends(require('portal.connect'))):
    return {'name': 'Cloudportal-backed', 'version': '1.0.0', 'api_version': 'v1',
            'providers': ['proxmox'], 'credential_types': ['proxmox', 'vmware', 'ssh', 'winrm', 'aws', 'azure', 'openstack', 'other'],
            'executors': ['terraform', 'ansible', 'opentofu'], 'openapi': '/openapi.json'}
