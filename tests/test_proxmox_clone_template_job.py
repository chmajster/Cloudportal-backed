from fastapi import HTTPException

from app.database import session
from app.jobs.worker import execute
from app.models import Job
from app.providers.proxmox import ProxmoxProvider

from test_proxmox_management import idem, resources


def test_clone_to_template_runs_in_background_and_is_visible_in_jobs(client, headers, monkeypatch):
    provider = resources(client, headers)
    vms = {
        ('pve01', 101): {
            'vmid': 101,
            'node': 'pve01',
            'name': 'source-vm',
            'status': 'stopped',
            'template': 0,
        },
    }
    calls = []

    def vm_status(_self, node, vmid):
        value = vms.get((str(node), int(vmid)))
        if value is None:
            raise HTTPException(404, 'VM not found')
        return dict(value)

    def clone_vm(_self, node, vmid, *, new_vm_id, name, target=None, full=True, storage=None, pool=None):
        calls.append(('clone', str(node), int(vmid), int(new_vm_id), str(target or node), name, full, storage))
        vms[(str(target or node), int(new_vm_id))] = {
            'vmid': int(new_vm_id),
            'node': str(target or node),
            'name': name,
            'status': 'stopped',
            'template': 0,
        }
        return 'UPID:clone-template-background'

    def convert_to_template(_self, node, vmid):
        calls.append(('template', str(node), int(vmid)))
        vms[(str(node), int(vmid))]['template'] = 1
        return 'UPID:convert-template-background'

    monkeypatch.setattr(ProxmoxProvider, 'vm_status', vm_status)
    monkeypatch.setattr(ProxmoxProvider, 'clone_vm', clone_vm)
    monkeypatch.setattr(ProxmoxProvider, 'convert_to_template', convert_to_template)
    monkeypatch.setattr(
        ProxmoxProvider,
        'task_status',
        lambda _self, _node, _upid: {'status': 'stopped', 'exitstatus': 'OK'},
    )

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"
    queued = client.post(base + '/clone', headers=idem(headers), json={
        'new_vm_id': 450,
        'name': 'ubuntu-golden-template',
        'target': 'pve02',
        'full': True,
        'storage': 'local-lvm',
        'convert_to_template': True,
    })
    assert queued.status_code == 202, queued.text
    job_id = queued.json()['job']['id']

    before = client.get('/api/v1/jobs/' + job_id, headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()['operation'] == 'proxmox.clone_template'
    assert before.json()['status'] == 'queued'

    execute(job_id)

    after = client.get('/api/v1/jobs/' + job_id, headers=headers)
    assert after.status_code == 200, after.text
    data = after.json()
    assert data['operation'] == 'proxmox.clone_template'
    assert data['status'] == 'successful'
    assert data['current_stage'] == 'proxmox.clone_template.completed'

    listed = client.get('/api/v1/jobs?limit=200', headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(
        item['id'] == job_id
        and item['operation'] == 'proxmox.clone_template'
        and item['status'] == 'successful'
        for item in listed.json()['items']
    )

    logs = client.get('/api/v1/jobs/' + job_id + '/logs?limit=200', headers=headers)
    assert logs.status_code == 200, logs.text
    messages = [item['message'] for item in logs.json()['items']]
    assert any('proxmox.clone_template.clone.started' in message for message in messages)
    assert any('proxmox.clone_template.template.started' in message for message in messages)
    assert any('proxmox.clone_template.completed' in message for message in messages)
    assert messages[-1].startswith('job.successful')

    assert ('clone', 'pve01', 101, 450, 'pve02', 'ubuntu-golden-template', True, 'local-lvm') in calls
    assert ('template', 'pve02', 450) in calls
    assert vms[('pve01', 101)]['template'] == 0
    assert vms[('pve02', 450)]['template'] == 1

    with session() as db:
        job = db.get(Job, job_id)
        assert job.payload['_proxmox_clone_template_result']['target_vm_id'] == 450
        assert job.payload['_proxmox_clone_template_result']['source_preserved'] is True
