import uuid


def auth(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def test_backup_and_restore_api(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Backup PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!backup', 'token_secret': 'example-private-value'},
    })
    assert credential.status_code == 201
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Backup PVE',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    }).json()

    archive = 'backup-nfs:backup/vzdump-qemu-101-2026_09_18-03_00_00.vma.zst'
    calls = []
    monkeypatch.setattr(ProxmoxProvider, 'backups', lambda self, node, storage, vm_id=None: [{
        'volid': archive, 'vmid': 101, 'size': 123456, 'format': 'vma.zst', 'content': 'backup',
    }])
    monkeypatch.setattr(
        ProxmoxProvider, 'backup_vm',
        lambda self, node, vmid, **kwargs: calls.append(('backup', vmid, kwargs)) or 'UPID:backup',
    )
    monkeypatch.setattr(
        ProxmoxProvider, 'restore_vm',
        lambda self, node, **kwargs: calls.append(('restore', kwargs)) or 'UPID:restore',
    )

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"
    listed = client.get(base + '/backups?storage=backup-nfs', headers=headers)
    assert listed.status_code == 200
    assert listed.json()['items'][0]['volid'] == archive

    backup = client.post(base + '/backups', headers=auth(headers), json={
        'storage': 'backup-nfs', 'mode': 'snapshot', 'compress': 'zstd', 'notes': 'nightly',
    })
    assert backup.status_code == 202 and backup.json()['task'] == 'UPID:backup'

    restore = client.post(
        f"/api/v1/providers/{provider['id']}/restore/pve02",
        headers=auth(headers),
        json={'vm_id': 202, 'archive': archive, 'storage': 'local-lvm', 'unique': True},
    )
    assert restore.status_code == 202 and restore.json()['task'] == 'UPID:restore'
    assert any(call[0] == 'restore' and call[1]['vm_id'] == 202 for call in calls)
