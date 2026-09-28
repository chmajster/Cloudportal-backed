from types import SimpleNamespace

from fastapi import HTTPException

from app.api import provider_diagnostics as diagnostics


class FakeProvider:
    id = 7
    name = 'LAB PVE'
    type = 'proxmox'
    credentials_id = 11


class FakeCredential:
    id = 11
    type = 'proxmox'
    endpoint = 'https://pve.example.test:8006'
    verify_ssl = True


def _pass(check_id, label, layer, *, critical=False):
    return diagnostics._check(
        check_id,
        label,
        layer,
        'pass',
        'ok',
        latency_ms=1,
        critical=critical,
    )


def test_ping_check_executes_real_ping_command_without_shell(monkeypatch):
    target = {
        'label': 'Proxmox VE',
        'host': 'pve.example.test',
        'port': 8006,
    }
    observed = {}

    monkeypatch.setattr(diagnostics.shutil, 'which', lambda command: '/usr/bin/ping' if command == 'ping' else None)

    def fake_run(args, **kwargs):
        observed['args'] = args
        observed['kwargs'] = kwargs
        return SimpleNamespace(returncode=0, stdout='64 bytes from pve.example.test: time=1.23 ms')

    monkeypatch.setattr(diagnostics.subprocess, 'run', fake_run)

    result = diagnostics._ping_check(target)

    assert result['status'] == 'pass'
    assert result['latency_ms'] == 1.23
    assert observed['args'] == ['/usr/bin/ping', '-c', '1', '-W', '2', 'pve.example.test']
    assert 'shell' not in observed['kwargs']


def test_tcp_timeout_is_reported_as_possible_firewall_or_routing_block(monkeypatch):
    target = {
        'label': 'Proxmox VE',
        'host': 'pve.example.test',
        'port': 8006,
    }

    def timeout(*args, **kwargs):
        raise diagnostics.socket.timeout()

    monkeypatch.setattr(diagnostics.socket, 'create_connection', timeout)

    result = diagnostics._tcp_check(target)

    assert result['status'] == 'fail'
    assert result['critical'] is True
    assert result['details']['reason'] == 'timeout'
    assert 'firewall' in result['message'].lower()
    assert 'routing' in result['message'].lower()


def test_diagnose_provider_quick_checks_nodes(monkeypatch):
    discovered = []

    class Adapter:
        def test(self):
            return {'ok': True, 'provider': 'proxmox', 'version': '9.0'}

        def discover(self, resource, node=None):
            discovered.append(resource)
            return [{'node': 'pve01', 'status': 'online'}]

    target = {
        'label': 'Proxmox VE',
        'url': 'https://pve.example.test:8006',
        'host': 'pve.example.test',
        'port': 8006,
        'scheme': 'https',
        'verify_ssl': True,
    }
    monkeypatch.setattr(diagnostics, '_network_targets', lambda credential: [target])
    monkeypatch.setattr(diagnostics, '_dns_check', lambda value: _pass('dns', 'DNS', 'dns', critical=True))
    monkeypatch.setattr(diagnostics, '_ping_check', lambda value: _pass('icmp', 'Ping', 'icmp'))
    monkeypatch.setattr(diagnostics, '_tcp_check', lambda value: _pass('tcp', 'TCP', 'tcp', critical=True))
    monkeypatch.setattr(diagnostics, '_tls_check', lambda value: _pass('tls', 'TLS', 'tls', critical=True))
    monkeypatch.setattr(diagnostics, '_http_check', lambda credential, value: _pass('http', 'HTTP', 'http', critical=True))
    monkeypatch.setattr(diagnostics, 'provider_for', lambda credential: Adapter())

    result = diagnostics.diagnose_provider(FakeProvider(), FakeCredential(), deep=False)

    assert result['status'] == 'ok'
    assert result['mode'] == 'quick'
    assert discovered == ['nodes']
    nodes = next(item for item in result['checks'] if item['id'] == 'discover_nodes')
    assert nodes['status'] == 'pass'
    assert nodes['details']['count'] == 1
    assert nodes['details']['sample'] == ['pve01']


def test_diagnose_provider_deep_checks_all_proxmox_readonly_resources(monkeypatch):
    discovered = []

    class Adapter:
        def test(self):
            return {'ok': True, 'provider': 'proxmox'}

        def discover(self, resource, node=None):
            discovered.append(resource)
            return []

    monkeypatch.setattr(diagnostics, '_network_targets', lambda credential: [])
    monkeypatch.setattr(diagnostics, 'provider_for', lambda credential: Adapter())

    result = diagnostics.diagnose_provider(FakeProvider(), FakeCredential(), deep=True)

    assert result['status'] == 'degraded'
    assert discovered == ['nodes', 'storages', 'networks', 'templates', 'pools', 'vms']
    assert all(
        next(item for item in result['checks'] if item['id'] == 'discover_' + resource)['status'] == 'pass'
        for resource in discovered
    )


def test_diagnose_provider_skips_discovery_when_authentication_fails(monkeypatch):
    class Adapter:
        def test(self):
            raise HTTPException(502, 'Token odrzucony')

        def discover(self, resource, node=None):
            raise AssertionError('discover must not run after failed authentication')

    monkeypatch.setattr(diagnostics, '_network_targets', lambda credential: [])
    monkeypatch.setattr(diagnostics, 'provider_for', lambda credential: Adapter())

    result = diagnostics.diagnose_provider(FakeProvider(), FakeCredential(), deep=True)

    assert result['status'] == 'failed'
    auth = next(item for item in result['checks'] if item['id'] == 'provider_auth')
    assert auth['status'] == 'fail'
    assert 'Token odrzucony' in auth['message']
    assert next(item for item in result['checks'] if item['id'] == 'discovery')['status'] == 'skipped'


def test_provider_diagnostics_endpoint_uses_saved_provider(system, monkeypatch):
    client, headers, _ = system
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Diagnostics PVE',
        'type': 'proxmox',
        'endpoint': 'https://127.0.0.1:8006',
        'username': 'root@pam',
        'verify_ssl': False,
        'secrets': {'token_id': 'root@pam!diag', 'token_secret': 'private-diagnostic-token'},
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Diagnostics LAB',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text

    monkeypatch.setattr(diagnostics, 'diagnose_provider', lambda p, c, deep=False: {
        'provider': {
            'id': p.id,
            'name': p.name,
            'type': p.type,
            'label': 'Proxmox VE',
            'credentials_id': p.credentials_id,
            'endpoint': c.endpoint,
            'verify_ssl': c.verify_ssl,
        },
        'mode': 'deep' if deep else 'quick',
        'status': 'ok',
        'summary': {'passed': 1, 'warnings': 0, 'failed': 0, 'skipped': 0},
        'checks': [_pass('provider_auth', 'Autoryzacja i API', 'provider', critical=True)],
    })

    response = client.post(
        f"/api/v1/diagnostics/providers/{provider.json()['id']}?deep=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == 'ok'
    assert payload['mode'] == 'deep'
    assert payload['provider']['name'] == 'Diagnostics LAB'
    assert 'private-diagnostic-token' not in response.text
