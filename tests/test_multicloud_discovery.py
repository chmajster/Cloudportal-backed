from app.providers.registry import PROVIDERS


def _credential_payload(kind):
    if kind == 'aws':
        return {'name': 'AWS discover', 'type': 'aws', 'secrets': {'access_key_id': 'AKIATESTVALUE', 'secret_access_key': 'private'}}
    if kind == 'azure':
        return {'name': 'Azure discover', 'type': 'azure', 'secrets': {'tenant_id': 'tenant', 'client_id': 'client', 'client_secret': 'private', 'subscription_id': 'sub'}}
    if kind == 'vmware':
        return {'name': 'VMware discover', 'type': 'vmware', 'endpoint': 'https://vc.example.com', 'username': 'administrator@vsphere.local', 'secrets': {'password': 'private'}}
    return {'name': 'OpenStack discover', 'type': 'openstack', 'endpoint': 'https://os.example.com:5000/v3', 'username': 'cloudportal', 'secrets': {'password': 'private', 'project_name': 'admin', 'domain_name': 'Default'}}


def test_registry_contains_native_multicloud_adapters():
    assert {'proxmox', 'aws', 'azure', 'openstack', 'vmware'} <= set(PROVIDERS)


def test_multicloud_discovery_is_routed_and_sanitized(client, headers, monkeypatch):
    for kind in ['aws', 'azure', 'vmware', 'openstack']:
        credential = client.post('/api/v1/credentials', headers=headers, json=_credential_payload(kind))
        assert credential.status_code == 201, credential.text
        provider = client.post('/api/v1/providers', headers=headers, json={
            'name': kind + '-native', 'type': kind, 'credentials_id': credential.json()['id'],
        })
        assert provider.status_code == 201, provider.text
        adapter_cls = PROVIDERS[kind]
        monkeypatch.setattr(adapter_cls, 'discover', lambda self, resource, node=None, k=kind: [{
            'id': k + '-1', 'vmid': k + '-1', 'name': k + '-vm', 'node': node or 'scope',
            'status': 'running', 'type': k, 'secret_internal': 'must-not-leak',
        }])
        response = client.get('/api/v1/providers/' + str(provider.json()['id']) + '/vms?node=eu-central-1', headers=headers)
        assert response.status_code == 200, response.text
        row = response.json()['items'][0]
        assert row['name'] == kind + '-vm'
        assert row['node'] == 'eu-central-1'
        assert 'secret_internal' not in row
