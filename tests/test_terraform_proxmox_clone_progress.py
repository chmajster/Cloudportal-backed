import time
from pathlib import Path

from app.executors.terraform import ProxmoxTerraformApplyTelemetry
from app.providers.proxmox import ProxmoxProvider


ROOT = Path(__file__).resolve().parents[1]


class FakeContext:
    def __init__(self):
        self.logs = []
        self.stages = []
        self.progress_updates = []

    def check(self):
        return None

    def log(self, message):
        self.logs.append(message)

    def stage(self, value):
        self.stages.append(value)

    def progress(self, percent=None, message=None, *, phase=None):
        self.progress_updates.append((percent, message, phase))


class FakeProxmox:
    def __init__(self, percent='42.5'):
        self.percent = percent
        self.task_queries = []
        self.log_queries = []

    def recent_tasks(self, *, vm_id=None, typefilter=None, limit=50):
        self.task_queries.append((vm_id, typefilter, limit))
        return [{
            'type': 'qmclone',
            'starttime': time.time(),
            'upid': 'UPID:pve:00000001:00000002:00000003:qmclone:121:root@pam:',
            'node': 'pve',
            'status': 'running',
        }]

    def task_log(self, node, upid, *, start=0, limit=500):
        self.log_queries.append((node, upid, start, limit))
        return [{'t': f'transferred 4.2 GiB of 10.0 GiB ({self.percent}%)'}]


def test_terraform_proxmox_vm_creation_reports_clone_stage_and_provider_percent():
    context = FakeContext()
    adapter = FakeProxmox()
    telemetry = ProxmoxTerraformApplyTelemetry(context, adapter, 121)

    telemetry.log('proxmox_virtual_environment_vm.vm: Creating...')

    assert context.logs == ['proxmox_virtual_environment_vm.vm: Creating...']
    assert context.stages == ['terraform.proxmox.clone']
    assert context.progress_updates[-1] == (42.5, 'Klonowanie VM w Proxmox', 'clone')
    assert adapter.task_queries == [(121, None, 25)]
    assert adapter.log_queries[0][0] == 'pve'


def test_terraform_proxmox_clone_completion_reports_100_percent():
    context = FakeContext()
    telemetry = ProxmoxTerraformApplyTelemetry(context, FakeProxmox(), 121)

    telemetry.log('module.vm.proxmox_virtual_environment_vm.vm: Creating...')
    telemetry.log('module.vm.proxmox_virtual_environment_vm.vm: Creation complete after 1m12s')

    assert telemetry.clone_started is True
    assert context.progress_updates[-1] == (
        100,
        'Klonowanie VM w Proxmox — zakończone',
        'clone',
    )


def test_recent_proxmox_tasks_can_be_filtered_by_vmid():
    provider = object.__new__(ProxmoxProvider)
    paths = []
    provider._get = lambda path: paths.append(path) or []

    assert provider.recent_tasks(vm_id=121, limit=25) == []
    assert len(paths) == 1
    assert paths[0].startswith('/cluster/tasks?')
    assert 'vmid=121' in paths[0]
    assert 'limit=25' in paths[0]


def test_vm_browser_does_not_turn_missing_progress_into_zero_percent():
    browser = (ROOT / 'app/web/features/deployments-vm-browser.js').read_text()
    stages = (ROOT / 'app/web/features/deployments-job-stage.js').read_text()
    terraform = (ROOT / 'app/executors/terraform.py').read_text()

    assert 'rawProgressValue === null' in browser
    assert 'Number.NaN' in browser
    assert "'terraform.proxmox.clone': 'Klonowanie VM w Proxmox'" in stages
    assert "context.stage('terraform.proxmox.clone')" in terraform
    assert "'Klonowanie VM w Proxmox'" in terraform
