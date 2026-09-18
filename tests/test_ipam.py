import uuid

from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute


def key(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def create_pool(client, headers, name='lab-net', cidr='192.0.2.0/29'):
    response = client.post('/api/v1/ipam/pools', headers=headers, json={
        'name': name,
        'cidr': cidr,
        'gateway': '192.0.2.1',
        'dns_servers': ['1.1.1.1', '8.8.8.8'],
        'excluded_addresses': ['192.0.2.2/32'],
    })
    assert response.status_code == 201, response.text
    return response.json()


def infrastructure(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'IPAM PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!terraform', 'token_secret': 'private-token-value'},
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'IPAM LAB',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    return credential.json(), provider.json()


def test_ipam_allocation_assignment_release_and_collision(client, headers):
    pool = create_pool(client, headers)

    first = client.post(
        f"/api/v1/ipam/pools/{pool['id']}/allocate",
        headers=key(headers),
        json={'hostname': 'vm01'},
    )
    assert first.status_code == 201, first.text
    assert first.json()['address'] == '192.0.2.3'
    assert first.json()['prefix_length'] == 29
    assert first.json()['gateway'] == '192.0.2.1'
    assert first.json()['status'] == 'reserved'

    collision = client.post(
        f"/api/v1/ipam/pools/{pool['id']}/allocate",
        headers=key(headers),
        json={'preferred_address': '192.0.2.3'},
    )
    assert collision.status_code == 409

    assigned = client.post(
        f"/api/v1/ipam/allocations/{first.json()['id']}/assign",
        headers=headers,
        json={'resource_id': 'deployment-1', 'hostname': 'vm01.example.test'},
    )
    assert assigned.status_code == 200
    assert assigned.json()['status'] == 'assigned'
    assert assigned.json()['resource_id'] == 'deployment-1'

    listed = client.get('/api/v1/ipam/allocations?resource_id=deployment-1', headers=headers)
    assert [row['id'] for row in listed.json()['items']] == [first.json()['id']]

    released = client.post(
        f"/api/v1/ipam/allocations/{first.json()['id']}/release",
        headers=headers,
    )
    assert released.status_code == 200 and released.json()['status'] == 'released'

    reused = client.post(
        f"/api/v1/ipam/pools/{pool['id']}/allocate",
        headers=key(headers),
        json={'preferred_address': '192.0.2.3'},
    )
    assert reused.status_code == 201 and reused.json()['address'] == '192.0.2.3'

    overlap = client.post('/api/v1/ipam/pools', headers=headers, json={
        'name': 'overlap',
        'cidr': '192.0.2.0/28',
        'gateway': '192.0.2.1',
    })
    assert overlap.status_code == 409


def test_ipam_pool_update_cannot_invalidate_active_allocation(client, headers):
    pool = create_pool(client, headers)
    allocation = client.post(
        f"/api/v1/ipam/pools/{pool['id']}/allocate",
        headers=key(headers),
        json={},
    )
    assert allocation.status_code == 201

    response = client.put(f"/api/v1/ipam/pools/{pool['id']}", headers=headers, json={
        'name': pool['name'],
        'cidr': pool['cidr'],
        'gateway': pool['gateway'],
        'dns_servers': pool['dns_servers'],
        'excluded_addresses': [allocation.json()['address']],
        'is_active': True,
    })
    assert response.status_code == 409


def test_blueprint_ipam_injects_static_ip_and_releases_after_destroy(client, headers, monkeypatch, tmp_path):
    credential, provider = infrastructure(client, headers)
    pool = client.post('/api/v1/ipam/pools', headers=headers, json={
        'name': 'blueprint-net',
        'cidr': '198.51.100.0/29',
        'gateway': '198.51.100.1',
    })
    assert pool.status_code == 201, pool.text

    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'IPAM hosts',
        'pattern': 'lab-web-{number}',
        'padding': 2,
    })
    assert scheme.status_code == 201, scheme.text

    blueprint = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'ipam-vm',
        'name': 'IPAM VM',
        'deployment': {
            'name': '{{ hostname }}',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'hostname_scheme_id': scheme.json()['id'],
            'ipam_pool_id': pool.json()['id'],
            'variables': {
                'name': '{{ hostname }}',
                'node': 'pve01',
                'template_id': 9000,
                'storage': 'local-lvm',
            },
        },
        'workflow': [
            {'id': 'name', 'type': 'generate_hostname'},
            {'id': 'ip', 'type': 'allocate_ip', 'depends_on': ['name']},
            {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['ip']},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        ],
    })
    assert blueprint.status_code == 201, blueprint.text

    created = client.post(
        f"/api/v1/blueprints/{blueprint.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body['variables']['ipv4_address'] == '198.51.100.2/29'
    assert body['variables']['ipv4_gateway'] == '198.51.100.1'
    assert body['workflow']['blueprint']['variables']['ip_address'] == '198.51.100.2'

    allocations = client.get(
        f"/api/v1/ipam/allocations?resource_id={body['id']}",
        headers=headers,
    ).json()['items']
    assert len(allocations) == 1 and allocations[0]['status'] == 'assigned'
    assert allocations[0]['hostname'] == 'lab-web-01'

    (tmp_path / 'terraform.tfstate').write_text(
        '{"outputs":{"vm_id":{"value":321}}}'
    )
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: tmp_path)
    execute(body['job']['id'])
    assert client.get(f"/api/v1/jobs/{body['job']['id']}", headers=headers).json()['status'] == 'successful'

    destroy = client.post(
        f"/api/v1/deployments/{body['id']}/destroy",
        headers=key(headers),
    )
    assert destroy.status_code == 202, destroy.text
    execute(destroy.json()['id'])

    released = client.get(
        f"/api/v1/ipam/allocations?resource_id={body['id']}",
        headers=headers,
    ).json()['items']
    assert released[0]['status'] == 'released'
    hostnames = client.get('/api/v1/hostnames?status=released', headers=headers).json()['items']
    assert any(row['resource_id'] == body['id'] for row in hostnames)


def test_static_ip_schema_rejects_gateway_outside_subnet(client, headers):
    credential, provider = infrastructure(client, headers)
    response = client.post('/api/v1/deployments', headers=key(headers), json={
        'name': 'bad-static-ip',
        'provider_id': provider['id'],
        'credentials_id': credential['id'],
        'variables': {
            'name': 'bad-static-ip',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
            'ipv4_address': '192.0.2.10/24',
            'ipv4_gateway': '198.51.100.1',
        },
    })
    assert response.status_code == 422
