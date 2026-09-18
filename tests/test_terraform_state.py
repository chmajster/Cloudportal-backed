import json

from app.database import session
from app.models import TerraformState
from app.terraform.state import persist_state, restore_state


def test_terraform_state_is_encrypted_and_portable(client, headers, tmp_path):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'State PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!state', 'token_secret': 'private-state-token'},
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'State PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()
    import uuid
    created = client.post('/api/v1/deployments', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'name': 'state-test',
        'provider_id': provider['id'],
        'credentials_id': credential['id'],
        'variables': {
            'name': 'state-test',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
        },
    })
    assert created.status_code == 202, created.text
    deployment = created.json()
    assert deployment['state_location'].startswith('database://')

    source = tmp_path / 'source'
    source.mkdir()
    raw = json.dumps({
        'version': 4,
        'serial': 7,
        'outputs': {'vm_id': {'value': 555, 'type': 'number'}},
    }).encode()
    (source / 'terraform.tfstate').write_bytes(raw)

    assert persist_state(deployment['id'], source)
    with session() as db:
        row = db.get(TerraformState, deployment['id'])
        assert row is not None
        assert raw not in row.encrypted_state
        assert row.version == 1

    target = tmp_path / 'target'
    target.mkdir()
    assert restore_state(deployment['id'], target)
    assert (target / 'terraform.tfstate').read_bytes() == raw

    updated = raw.replace(b'"serial": 7', b'"serial": 8')
    (target / 'terraform.tfstate').write_bytes(updated)
    assert persist_state(deployment['id'], target)
    with session() as db:
        assert db.get(TerraformState, deployment['id']).version == 2
