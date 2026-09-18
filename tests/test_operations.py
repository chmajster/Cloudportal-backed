import hashlib
import hmac
import json
import uuid
from datetime import timedelta

from app.config import settings
from app.database import session
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Deployment, Job, ScheduledOperation, User, WebhookDelivery, now
from app.operations.service import deliver_webhooks_once, materialize_scheduled_jobs


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def deployment(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Ops PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!ops', 'token_secret': 'private-ops-token'},
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Ops PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()
    response = client.post('/api/v1/deployments', headers=idem(headers), json={
        'name': 'scheduled-vm',
        'provider_id': provider['id'],
        'credentials_id': credential['id'],
        'variables': {
            'name': 'scheduled-vm',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
        },
    })
    assert response.status_code == 202, response.text
    return response.json()


def test_schedule_materializes_durable_job_and_rechecks_user(client, headers, monkeypatch, tmp_path):
    created = deployment(client, headers)
    with session() as db:
        dep = db.get(Deployment, created['id'])
        initial = db.get(Job, dep.active_job_id)
        initial.status = 'successful'
        dep.active_job_id = None
        dep.status = 'successful'
        db.commit()

    future = (now() + timedelta(hours=1)).isoformat() + 'Z'
    response = client.post('/api/v1/schedules', headers=headers, json={
        'name': 'hourly-plan',
        'deployment_id': created['id'],
        'operation': 'terraform.plan',
        'next_run_at': future,
        'interval_seconds': 3600,
    })
    assert response.status_code == 201, response.text
    schedule = response.json()

    with session() as db:
        row = db.get(ScheduledOperation, schedule['id'])
        row.next_run_at = now() - timedelta(seconds=1)
        db.commit()

    materialize_scheduled_jobs()

    with session() as db:
        row = db.get(ScheduledOperation, schedule['id'])
        assert row.last_run_at is not None
        assert row.is_active
        job = db.query(Job).filter(Job.source == 'Scheduler', Job.deployment_id == created['id']).one()
        assert job.token_id is None
        job_id = job.id

    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: tmp_path)
    execute(job_id)
    result = client.get('/api/v1/jobs/' + job_id, headers=headers)
    assert result.status_code == 200
    assert result.json()['status'] == 'successful'

    with session() as db:
        schedule_owner = db.get(ScheduledOperation, schedule['id']).created_by
        user = db.get(User, schedule_owner)
        user.is_active = False
        row = db.get(ScheduledOperation, schedule['id'])
        row.next_run_at = now() - timedelta(seconds=1)
        db.commit()

    materialize_scheduled_jobs()
    with session() as db:
        next_job = db.query(Job).filter(
            Job.source == 'Scheduler',
            Job.deployment_id == created['id'],
            Job.id != job_id,
        ).order_by(Job.created_at.desc()).first()
        assert next_job is not None
        next_id = next_job.id

    execute(next_id)
    with session() as db:
        failed = db.get(Job, next_id)
        assert failed.status == 'failed'
        assert failed.error == 'Scheduled job owner is disabled or locked'


def test_signed_webhook_delivery_and_secret_redaction(client, headers, monkeypatch):
    monkeypatch.setattr(settings(), 'webhook_allowed_hosts', ['hooks.example.test'])

    response = client.post('/api/v1/webhooks', headers=idem(headers), json={
        'name': 'CI hook',
        'url': 'https://hooks.example.test/cloudportal',
        'events': ['job.failed'],
    })
    assert response.status_code == 201, response.text
    endpoint = response.json()
    secret = endpoint['secret']
    assert len(secret) > 32

    listed = client.get('/api/v1/webhooks', headers=headers)
    assert listed.status_code == 200
    assert 'secret' not in listed.text

    with session() as db:
        row = WebhookDelivery(
            endpoint_id=endpoint['id'],
            event='job.failed',
            resource_id='job-test',
            payload={
                'event': 'job.failed',
                'job': {'id': 'job-test', 'status': 'failed'},
                'created_at': now().isoformat() + 'Z',
            },
        )
        db.add(row)
        db.commit()
        delivery_id = row.id

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured['client'] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            captured['url'] = url
            captured['body'] = content
            captured['headers'] = headers
            return Response()

    monkeypatch.setattr('app.operations.service.httpx.Client', Client)
    deliver_webhooks_once()

    expected = 'sha256=' + hmac.new(
        secret.encode(),
        captured['body'],
        hashlib.sha256,
    ).hexdigest()
    assert captured['url'] == 'https://hooks.example.test/cloudportal'
    assert captured['headers']['X-Cloudportal-Delivery'] == delivery_id
    assert hmac.compare_digest(captured['headers']['X-Cloudportal-Signature'], expected)

    with session() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        assert delivery.status == 'delivered'
        assert delivery.delivered_at is not None


def test_webhook_rejects_non_allowlisted_host(client, headers, monkeypatch):
    monkeypatch.setattr(settings(), 'webhook_allowed_hosts', ['allowed.example.test'])
    response = client.post('/api/v1/webhooks', headers=idem(headers), json={
        'name': 'Blocked hook',
        'url': 'https://blocked.example.test/hook',
        'events': ['job.successful'],
    })
    assert response.status_code == 422
