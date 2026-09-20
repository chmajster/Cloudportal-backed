import json
import uuid

from app.config import settings
from app.database import session
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Deployment, Job, now


def _deployment(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Offline PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {
            'token_id': 'root@pam!cloudportal',
            'token_secret': 'secret-value',
        },
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Offline LAB',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    response = client.post(
        '/api/v1/deployments',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json={
            'name': 'queued-vm',
            'provider_id': provider.json()['id'],
            'credentials_id': credential.json()['id'],
            'variables': {
                'name': 'queued-vm',
                'node': 'pve',
                'template_id': 9000,
                'storage': 'local-lvm',
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_proxmox_job_waits_in_database_and_runs_after_reconnect(client, headers, monkeypatch, tmp_path):
    deployment = _deployment(client, headers)
    cfg = settings()
    cfg.provider_offline_queue_enabled = True
    cfg.provider_retry_base_seconds = 5
    cfg.provider_retry_max_seconds = 5

    probes = iter([
        {'ok': False, 'retryable': True, 'reason': 'unreachable'},
        {'ok': True, 'retryable': False, 'reason': None},
    ])

    class Provider:
        def execution_availability(self):
            return next(probes)

    monkeypatch.setattr('app.jobs.worker.provider_for', lambda credential: Provider())

    called = []
    workspace = tmp_path / 'terraform-workspace'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(json.dumps({
        'outputs': {'vm_id': {'value': 321}},
    }))
    monkeypatch.setattr(
        TerraformExecutor,
        'execute',
        lambda *args: called.append('terraform') or workspace,
    )

    job_id = deployment['job']['id']
    execute(job_id)

    waiting_job = client.get('/api/v1/jobs/' + job_id, headers=headers)
    assert waiting_job.status_code == 200
    waiting = waiting_job.json()
    assert waiting['status'] == 'queued'
    assert waiting['provider_waiting'] is True
    assert waiting['provider_retry_attempts'] == 1
    assert waiting['provider_next_retry_at'] is not None
    assert called == []

    waiting_deployment = client.get('/api/v1/deployments/' + deployment['id'], headers=headers).json()
    assert waiting_deployment['status'] == 'waiting_provider'
    assert waiting_deployment['active_job_id'] == job_id

    with session() as db:
        row = db.get(Job, job_id)
        assert row.payload['_provider_wait']['reason'] == 'unreachable'
        assert row.payload['_provider_wait']['attempts'] == 1
        assert any('provider.waiting' in log.message for log in row_logs(db, job_id))

    execute(job_id)

    finished = client.get('/api/v1/jobs/' + job_id, headers=headers).json()
    assert finished['status'] == 'successful'
    assert finished['provider_waiting'] is False
    assert finished['provider_retry_attempts'] == 0
    assert called == ['terraform']

    completed_deployment = client.get('/api/v1/deployments/' + deployment['id'], headers=headers).json()
    assert completed_deployment['status'] == 'successful'
    assert completed_deployment['active_job_id'] is None


def row_logs(db, job_id):
    from sqlalchemy import select
    from app.models import JobLog
    return db.scalars(select(JobLog).where(JobLog.job_id == job_id)).all()


def test_dispatcher_skips_provider_wait_until_retry_time(client, headers):
    from datetime import timedelta
    from app.jobs.queue import provider_retry_ready

    deployment = _deployment(client, headers)
    with session() as db:
        job = db.get(Job, deployment['job']['id'])
        payload = dict(job.payload or {})
        payload['_provider_wait'] = {
            'attempts': 2,
            'reason': 'unreachable',
            'next_attempt_at': (now() + timedelta(minutes=5)).isoformat(),
        }
        job.payload = payload
        db.commit()
        assert provider_retry_ready(job) is False

        payload['_provider_wait']['next_attempt_at'] = (now() - timedelta(seconds=1)).isoformat()
        job.payload = dict(payload)
        db.commit()
        assert provider_retry_ready(job) is True


def test_provider_offline_retry_limit_becomes_terminal_failure(client, headers, monkeypatch):
    deployment = _deployment(client, headers)
    cfg = settings()
    cfg.provider_offline_queue_enabled = True
    cfg.provider_retry_base_seconds = 5
    cfg.provider_retry_max_seconds = 5
    monkeypatch.setattr(cfg, 'provider_retry_max_attempts', 1)

    class Provider:
        def execution_availability(self):
            return {'ok': False, 'retryable': True, 'reason': 'still-offline'}

    monkeypatch.setattr('app.jobs.worker.provider_for', lambda credential: Provider())

    job_id = deployment['job']['id']
    execute(job_id)
    first = client.get('/api/v1/jobs/' + job_id, headers=headers).json()
    assert first['status'] == 'queued'
    assert first['provider_retry_attempts'] == 1

    execute(job_id)
    terminal = client.get('/api/v1/jobs/' + job_id, headers=headers).json()
    assert terminal['status'] == 'failed'
    assert terminal['provider_waiting'] is False
    assert terminal['provider_retry_attempts'] == 0
    assert terminal['provider_next_retry_at'] is None
    assert 'retry attempts' in terminal['error']
