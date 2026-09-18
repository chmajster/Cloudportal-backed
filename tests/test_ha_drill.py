import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'e2e-ha-failover.py'
SPEC = importlib.util.spec_from_file_location('e2e_ha_failover', SCRIPT)
ha = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ha)


def test_ssh_command_is_fixed_and_strict(tmp_path):
    key = tmp_path / 'id'
    hosts = tmp_path / 'known_hosts'
    key.write_text('x')
    hosts.write_text('x')
    command = ha.ssh_command('worker-a.example.com', 'cloudportal', key, hosts, 'systemctl is-active test')
    assert command[0] == 'ssh'
    assert 'BatchMode=yes' in command
    assert 'StrictHostKeyChecking=yes' in command
    assert command[-2] == 'cloudportal@worker-a.example.com'
    assert command[-1] == 'systemctl is-active test'


@pytest.mark.parametrize('host,user', [
    ('worker;rm', 'cloudportal'),
    ('worker.example.com', 'bad user'),
])
def test_ssh_command_rejects_unsafe_identity(host, user, tmp_path):
    with pytest.raises(ValueError):
        ha.ssh_command(host, user, tmp_path / 'id', tmp_path / 'known', 'true')
