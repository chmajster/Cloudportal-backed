from datetime import timedelta
import uuid

from app.database import session
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Credential, Job, now


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def test_credential_lifecycle_metadata_and_runtime_expiry(client, headers, monkeypatch):
    future = now() + timedelta(days=30)
    rotation = now() + timedelta(days=10)
    created = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Lifecycle PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'expires_at': future.isoformat() + 'Z',
        'rotation_due_at': rotation.isoformat() + 'Z',
        'secrets': {'token_id': 'root@pam!lifecycle', 'token_secret': 'private-lifecycle-token'},
    })
    assert created.status_code == 201, created.text
    credential = created.json()
    assert credential['expires_at'] is not None
    assert credential['rotation_due_at'] is not None
    assert credential['secret_updated_at'] is not None

    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Lifecycle PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    })
    assert provider.status_code == 201, provider.text

    deployment = client.post('/api/v1/deployments', headers=idem(headers), json={
        'name': 'lifecycle-vm',
        'provider_id': provider.json()['id'],
        'credentials_id': credential['id'],
        'variables': {
            'name': 'lifecycle-vm',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
        },
    })
    assert deployment.status_code == 202, deployment.text
    job_id = deployment.json()['job']['id']

    with session() as db:
        row = db.get(Credential, credential['id'])
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()

    called = []
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: called.append(True))
    execute(job_id)

    with session() as db:
        job = db.get(Job, job_id)
        assert job.status == 'failed'
        assert 'credential expired' in job.error.lower()
    assert called == []

    blocked = client.post('/api/v1/deployments', headers=idem(headers), json={
        'name': 'blocked-vm',
        'provider_id': provider.json()['id'],
        'credentials_id': credential['id'],
        'variables': {
            'name': 'blocked-vm',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
        },
    })
    assert blocked.status_code == 409
