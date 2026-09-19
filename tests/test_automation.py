import uuid


def resources(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'PVE', 'type': 'proxmox', 'endpoint': 'https://pve.example.com:8006', 'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!terraform', 'token_secret': 'very-private-secret-value'},
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'LAB', 'type': 'proxmox', 'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    payload = {'name': 'test', 'provider_id': provider.json()['id'], 'credentials_id': credential.json()['id'],
               'variables': {'name': 'vm01', 'node': 'pve', 'template_id': 9000, 'storage': 'local-lvm'}}
    return credential.json(), provider.json(), payload


def key(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def portal_headers(client, headers):
    user = client.post('/api/v1/users', headers=headers, json={
        'username': 'portal-service', 'email': 'portal-service@example.com',
        'password': 'service-password-1234', 'is_service_account': True,
    }).json()
    role = next(row for row in client.get('/api/v1/roles', headers=headers).json()['items'] if row['name'] == 'Portal Service')
    assert client.put(f"/api/v1/users/{user['id']}/roles", headers=headers, json={'role_ids': [role['id']]}).status_code == 200
    token = client.post('/api/v1/tokens', headers=headers, json={
        'name': 'CloudPortal', 'user_id': user['id'], 'scopes': ['portal.connect'],
    }).json()['token']
    return {**headers, 'X-Portal-Source': 'CloudPortal', 'X-Portal-Token': token}


def test_hostname_manager_reserves_unique_names(client, headers):
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Linux production', 'pattern': '{location}-{env}-{role}-{number}', 'padding': 3,
    })
    assert scheme.status_code == 201, scheme.text
    body = {'scheme_id': scheme.json()['id'], 'values': {'location': 'wro', 'env': 'prod', 'role': 'web'}}
    first = client.post('/api/v1/hostnames/generate', headers=headers, json=body)
    second = client.post('/api/v1/hostnames/generate', headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()['hostname'] == 'wro-prod-web-001'
    assert second.json()['hostname'] == 'wro-prod-web-002'
    released = client.post('/api/v1/hostnames/' + first.json()['reservation']['id'] + '/release', headers=headers)
    assert released.status_code == 200 and released.json()['status'] == 'released'



def test_hostname_scheme_edit_cannot_reset_sequence(client, headers):
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'SRL servers', 'pattern': 'srl{number}', 'padding': 3,
    })
    assert scheme.status_code == 201, scheme.text

    first = client.post('/api/v1/hostnames/generate', headers=headers, json={
        'scheme_id': scheme.json()['id'], 'values': {},
    })
    assert first.status_code == 200
    assert first.json()['hostname'] == 'srl001'

    current = client.get('/api/v1/hostname-schemes/' + str(scheme.json()['id']), headers=headers).json()
    assert current['next_number'] == 2

    edited = client.put('/api/v1/hostname-schemes/' + str(scheme.json()['id']), headers=headers, json={
        'name': 'SRL production servers',
        'pattern': 'srl{number}',
        'next_number': current['next_number'],
        'padding': 4,
        'is_active': True,
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()['next_number'] == 2
    assert edited.json()['padding'] == 4

    rollback = client.put('/api/v1/hostname-schemes/' + str(scheme.json()['id']), headers=headers, json={
        'name': 'SRL production servers',
        'pattern': 'srl{number}',
        'next_number': 1,
        'padding': 4,
        'is_active': True,
    })
    assert rollback.status_code == 409

    second = client.post('/api/v1/hostnames/generate', headers=headers, json={
        'scheme_id': scheme.json()['id'], 'values': {},
    })
    assert second.status_code == 200
    assert second.json()['hostname'] == 'srl0002'


def test_blueprint_validates_dag_visibility_and_compiles_deployment(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Application hosts', 'pattern': '{env}-{role}-{number}', 'padding': 3,
    }).json()
    payload = {
        'slug': 'ubuntu-web', 'name': 'Ubuntu Web Server', 'description': 'Standard self-service server',
        'visibility': {'backend': True, 'cloudportal': True, 'api': True},
        'variables_schema': {
            'cpu': {'type': 'integer', 'required': True, 'min': 1, 'max': 8, 'default': 2},
            'memory': {'type': 'integer', 'required': True, 'min': 512, 'max': 16384, 'default': 4096},
        },
        'deployment': {
            'name': '{{ hostname }}', 'provider_id': provider['id'], 'credentials_id': credential['id'],
            'hostname_scheme_id': scheme['id'], 'variables': {
                **deployment_payload['variables'], 'name': '{{ hostname }}', 'cpu': '{{ cpu }}', 'memory': '{{ memory }}',
            },
        },
        'workflow': [
            {'id': 'hostname', 'type': 'generate_hostname'},
            {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['hostname']},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        ],
    }
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    signed_portal_headers = portal_headers(client, headers)
    available = client.get('/api/v1/blueprints?available=true', headers=signed_portal_headers)
    assert [row['slug'] for row in available.json()['items']] == ['ubuntu-web']
    execution = client.post('/api/v1/blueprints/%s/execute' % created.json()['id'], headers={
        **key(signed_portal_headers)}, json={
        'variables': {'cpu': 4, 'memory': 8192}, 'hostname_values': {'env': 'prod', 'role': 'web'},
    })
    assert execution.status_code == 202, execution.text
    assert execution.json()['name'] == 'prod-web-001'
    assert execution.json()['variables']['cpu'] == 4
    assert execution.json()['workflow']['blueprint']['version'] == 1
    assert execution.json()['job']['source'] == 'CloudPortal'
    reservations = client.get('/api/v1/hostnames?status=assigned', headers=headers).json()['items']
    assert reservations[0]['resource_id'] == execution.json()['id']


def test_cloudportal_source_requires_service_authentication(client, headers):
    response = client.get('/api/v1/blueprints?available=true', headers={**headers, 'X-Portal-Source': 'CloudPortal'})
    assert response.status_code == 401


def test_blueprint_rejects_cycle(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    payload = {
        'slug': 'bad-dag', 'name': 'Bad DAG',
        'deployment': {'name': 'bad-dag', 'provider_id': provider['id'], 'credentials_id': credential['id'],
                       'variables': deployment_payload['variables']},
        'workflow': [
            {'id': 'a', 'type': 'clone_vm', 'depends_on': ['b']},
            {'id': 'b', 'type': 'terraform_apply', 'depends_on': ['a']},
        ],
    }
    response = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert response.status_code == 422
    assert 'acyclic' in response.text


def test_blueprint_reuses_saved_hostname_tags_and_cloud_init(client, headers):
    credential, provider, _ = resources(client, headers)
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Production web',
        'pattern': '{env}-{role}-{number}',
        'padding': 3,
    })
    assert scheme.status_code == 201, scheme.text

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'fast-proxmox-vm',
        'name': 'Fast Proxmox VM',
        'variables_schema': {},
        'deployment': {
            'name': '{{ hostname }}',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'template': 'proxmox-vm',
            'hostname_scheme_id': scheme.json()['id'],
            'hostname_values': {'env': 'prod', 'role': 'web'},
            'variables': {
                'name': '{{ hostname }}',
                'node': 'pve01',
                'template_id': 9000,
                'template_node': 'pve01',
                'cpu': 4,
                'memory': 8192,
                'disk': 80,
                'network': 'vmbr20',
                'storage': 'local-lvm',
                'ssh_username': 'clouduser',
                'dns_servers': ['1.1.1.1', '8.8.8.8'],
                'dns_domain': 'lab.example.com',
                'tags': ['production', 'web', 'linux'],
            },
        },
        'workflow': [
            {'id': 'hostname', 'type': 'generate_hostname'},
            {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['hostname']},
            {'id': 'cloud_init', 'type': 'cloud_init', 'depends_on': ['clone']},
            {'id': 'tags', 'type': 'set_tags', 'depends_on': ['cloud_init']},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['tags']},
        ],
    })
    assert created.status_code == 201, created.text

    pattern = client.get('/api/v1/hostname-schemes/' + str(scheme.json()['id']), headers=headers)
    assert pattern.status_code == 200
    assert pattern.json()['pattern'] == '{env}-{role}-{number}'

    execution = client.post(
        '/api/v1/blueprints/' + str(created.json()['id']) + '/execute',
        headers=key(headers),
        json={},
    )
    assert execution.status_code == 202, execution.text
    result = execution.json()
    assert result['name'] == 'prod-web-001'
    assert result['variables']['name'] == 'prod-web-001'
    assert result['variables']['tags'] == ['linux', 'production', 'web']
    assert result['variables']['dns_servers'] == ['1.1.1.1', '8.8.8.8']
    assert result['variables']['dns_domain'] == 'lab.example.com'


def test_blueprint_hostname_defaults_must_match_pattern(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Number only',
        'pattern': 'vm-{number}',
    }).json()
    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'invalid-hostname-default',
        'name': 'Invalid hostname default',
        'deployment': {
            'name': '{{ hostname }}',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'hostname_scheme_id': scheme['id'],
            'hostname_values': {'env': 'prod'},
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {'id': 'hostname', 'type': 'generate_hostname'},
            {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['hostname']},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        ],
    })
    assert response.status_code == 422
    assert 'tokens not used' in response.text
