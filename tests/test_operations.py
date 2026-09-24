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
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.quotas.service import release_job_reservation, set_project_limit
from app.resource_scope.authorization import Scope
from app.tenancy.permissions import DEFAULT_TENANT_ID


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
        assert next_job is None
        row = db.get(ScheduledOperation, schedule['id'])
        assert row.is_active is False
        assert row.last_error == 'Schedule owner is disabled or locked'


def test_scheduled_apply_snapshots_custom_ansible_playbook(client, headers):
    created = deployment(client, headers)
    custom = client.post('/api/v1/ansible/custom-playbooks', headers=headers, json={
        'id': 'scheduled-custom',
        'name': 'Scheduled custom',
        'category': 'Własne',
        'transport': 'ssh',
        'variables': {},
        'wait_for_connection': False,
        'validate_after': False,
        'content': (
            '- name: Scheduled v1\n'
            '  hosts: all\n'
            '  gather_facts: false\n'
            '  tasks:\n'
            '    - ansible.builtin.debug:\n'
            '        msg: "scheduled-v1"\n'
        ),
    })
    assert custom.status_code == 201, custom.text
    guest = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Scheduled guest SSH',
        'type': 'ssh',
        'endpoint': 'ssh://192.0.2.91:22',
        'username': 'clouduser',
        'secrets': {'password': 'scheduled-custom-password'},
    })
    assert guest.status_code == 201, guest.text

    with session() as db:
        dep = db.get(Deployment, created['id'])
        initial = db.get(Job, dep.active_job_id)
        initial.status = 'successful'
        dep.active_job_id = None
        dep.status = 'successful'
        run = {
            'playbook': 'scheduled-custom',
            'credentials_id': guest.json()['id'],
            'inventory': None,
            'variables': {},
        }
        dep.workflow = {'ansible': run, 'ansible_runs': [run]}
        db.commit()

    response = client.post('/api/v1/schedules', headers=headers, json={
        'name': 'scheduled-custom-apply',
        'deployment_id': created['id'],
        'operation': 'terraform.apply',
        'next_run_at': (now() + timedelta(hours=1)).isoformat() + 'Z',
    })
    assert response.status_code == 201, response.text
    with session() as db:
        schedule = db.get(ScheduledOperation, response.json()['id'])
        schedule.next_run_at = now() - timedelta(seconds=1)
        db.commit()

    materialize_scheduled_jobs()

    with session() as db:
        job = db.query(Job).filter(
            Job.source == 'Scheduler',
            Job.deployment_id == created['id'],
            Job.operation == 'terraform.apply',
        ).one()
        snapshot = job.payload['_ansible_playbook_snapshots']['scheduled-custom']
        assert snapshot['version'] == 1
        assert 'scheduled-v1' in snapshot['content']

    updated = client.put('/api/v1/ansible/custom-playbooks/scheduled-custom', headers=headers, json={
        'id': 'scheduled-custom',
        'name': 'Scheduled custom',
        'category': 'Własne',
        'transport': 'ssh',
        'variables': {},
        'wait_for_connection': False,
        'validate_after': False,
        'content': (
            '- name: Scheduled v2\n'
            '  hosts: all\n'
            '  gather_facts: false\n'
            '  tasks:\n'
            '    - ansible.builtin.debug:\n'
            '        msg: "scheduled-v2"\n'
        ),
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()['version'] == 2

    with session() as db:
        job = db.query(Job).filter(
            Job.source == 'Scheduler',
            Job.deployment_id == created['id'],
            Job.operation == 'terraform.apply',
        ).one()
        snapshot = job.payload['_ansible_playbook_snapshots']['scheduled-custom']
        assert snapshot['version'] == 1
        assert 'scheduled-v1' in snapshot['content']
        assert 'scheduled-v2' not in snapshot['content']


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


def test_scheduled_blueprint_apply_waits_for_approval(client, headers):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 48},
    )
    assert setting.status_code == 200, setting.text

    created = deployment(client, headers)
    with session() as db:
        dep = db.get(Deployment, created['id'])
        initial = db.get(Job, dep.active_job_id)
        initial.status = 'successful'
        dep.active_job_id = None
        dep.status = 'successful'
        dep.workflow = {
            'blueprint': {
                'id': None,
                'requires_approval': True,
                'steps': [{'id': 'apply', 'type': 'terraform_apply'}],
            },
        }
        db.commit()

    response = client.post('/api/v1/schedules', headers=headers, json={
        'name': 'approved-apply',
        'deployment_id': created['id'],
        'operation': 'terraform.apply',
        'next_run_at': (now() + timedelta(hours=1)).isoformat() + 'Z',
    })
    assert response.status_code == 201, response.text

    with session() as db:
        schedule = db.get(ScheduledOperation, response.json()['id'])
        schedule.next_run_at = now() - timedelta(seconds=1)
        db.commit()

    materialize_scheduled_jobs()

    with session() as db:
        job = db.query(Job).filter(
            Job.source == 'Scheduler',
            Job.deployment_id == created['id'],
            Job.operation == 'terraform.apply',
        ).one()
        assert job.status == 'waiting_approval'
        assert job.payload['_approval']['status'] == 'pending'
        deployment_row = db.get(Deployment, created['id'])
        assert deployment_row.status == 'waiting_approval'
        job_id = job.id

    execute(job_id)
    assert client.get('/api/v1/jobs/' + job_id, headers=headers).json()['status'] == 'waiting_approval'



def test_quota_rejected_one_shot_schedule_does_not_repeat(client, headers):
    created = deployment(client, headers)
    with session() as db:
        dep = db.get(Deployment, created['id'])
        initial = db.get(Job, dep.active_job_id)
        release_job_reservation(db, initial)
        initial.status = 'successful'
        dep.active_job_id = None
        dep.status = 'successful'
        set_project_limit(db, Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID), 'vm_count', 0)
        db.commit()

    response = client.post('/api/v1/schedules', headers=headers, json={
        'name': 'quota-denied-apply',
        'deployment_id': created['id'],
        'operation': 'terraform.apply',
        'next_run_at': (now() + timedelta(hours=1)).isoformat() + 'Z',
    })
    assert response.status_code == 201, response.text

    with session() as db:
        row = db.get(ScheduledOperation, response.json()['id'])
        row.next_run_at = now() - timedelta(seconds=1)
        db.commit()

    materialize_scheduled_jobs()
    materialize_scheduled_jobs()

    with session() as db:
        row = db.get(ScheduledOperation, response.json()['id'])
        jobs = db.query(Job).filter(
            Job.source == 'Scheduler',
            Job.deployment_id == created['id'],
            Job.operation == 'terraform.apply',
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status == 'failed'
        assert row.is_active is False
        assert row.last_run_at is not None
        assert row.last_error
