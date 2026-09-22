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
    body = {'scheme_id': scheme.json()['id'], 'values': {'env': 'prod'}}
    first = client.post('/api/v1/hostnames/generate', headers=headers, json=body)
    second = client.post('/api/v1/hostnames/generate', headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()['hostname'] == 'wro-prod-server-001'
    assert second.json()['hostname'] == 'wro-prod-server-002'
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



def test_blueprint_manager_role_is_required_and_dedicated_to_one_template(client, headers):
    credential, provider, deployment_payload = resources(client, headers)

    manager_role = client.post('/api/v1/roles', headers=headers, json={
        'name': 'Template A Manager',
        'permissions': ['blueprints.read', 'blueprints.update', 'blueprints.delete'],
    })
    other_role = client.post('/api/v1/roles', headers=headers, json={
        'name': 'Template B Manager',
        'permissions': ['blueprints.read', 'blueprints.update', 'blueprints.delete'],
    })
    assert manager_role.status_code == other_role.status_code == 201

    manager_user = client.post('/api/v1/users', headers=headers, json={
        'username': 'template-manager',
        'email': 'template-manager@example.com',
        'password': 'strong-password-1234',
    })
    other_user = client.post('/api/v1/users', headers=headers, json={
        'username': 'other-template-manager',
        'email': 'other-template-manager@example.com',
        'password': 'strong-password-1234',
    })
    assert manager_user.status_code == other_user.status_code == 201

    assert client.put(
        '/api/v1/users/' + str(manager_user.json()['id']) + '/roles',
        headers=headers,
        json={'role_ids': [manager_role.json()['id']]},
    ).status_code == 200
    assert client.put(
        '/api/v1/users/' + str(other_user.json()['id']) + '/roles',
        headers=headers,
        json={'role_ids': [other_role.json()['id']]},
    ).status_code == 200

    manager_login = client.post('/api/v1/auth/login', json={
        'username': 'template-manager', 'password': 'strong-password-1234',
    }).json()
    other_login = client.post('/api/v1/auth/login', json={
        'username': 'other-template-manager', 'password': 'strong-password-1234',
    }).json()
    manager_headers = {'Authorization': 'Bearer ' + manager_login['access_token']}
    other_headers = {'Authorization': 'Bearer ' + other_login['access_token']}

    payload = {
        'slug': 'role-protected-template',
        'name': 'Role protected template',
        'manager_role_ids': [manager_role.json()['id']],
        'deployment': {
            'name': 'role-protected-template',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    }
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    blueprint = created.json()
    assert blueprint['manager_role_ids'] == [manager_role.json()['id']]

    denied = client.put(
        '/api/v1/blueprints/' + str(blueprint['id']),
        headers=other_headers,
        json={**payload, 'description': 'unauthorized edit'},
    )
    assert denied.status_code == 403
    assert 'dedicated manager role' in denied.text

    allowed = client.put(
        '/api/v1/blueprints/' + str(blueprint['id']),
        headers=manager_headers,
        json={**payload, 'description': 'authorized edit'},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['description'] == 'authorized edit'

    second = client.post('/api/v1/blueprints', headers=headers, json={
        **payload,
        'slug': 'second-role-protected-template',
        'name': 'Second role protected template',
    })
    assert second.status_code == 409
    assert 'already dedicated to another template' in second.text

    denied_delete = client.delete('/api/v1/blueprints/' + str(blueprint['id']), headers=other_headers)
    assert denied_delete.status_code == 403
    allowed_delete = client.delete('/api/v1/blueprints/' + str(blueprint['id']), headers=manager_headers)
    assert allowed_delete.status_code == 200



def test_blueprint_quick_toggle(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'toggle-template',
        'name': 'Toggle template',
        'deployment': {
            'name': 'toggle-template',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text
    blueprint = created.json()

    disabled = client.put(
        f"/api/v1/blueprints/{blueprint['id']}/enabled",
        headers=headers,
        json={'enabled': False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()['is_active'] is False

    enabled = client.put(
        f"/api/v1/blueprints/{blueprint['id']}/enabled",
        headers=headers,
        json={'enabled': True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()['is_active'] is True


def test_blueprint_guest_ssh_credential_is_injected_into_cloud_init(client, headers, monkeypatch):
    from app.credentials.ssh import generate_ed25519_key_pair

    provider_credential, provider, deployment_payload = resources(client, headers)
    private_key, public_key = generate_ed25519_key_pair()
    guest = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'VM bootstrap SSH',
        'type': 'ssh',
        'endpoint': 'ssh://vm.example.com:22',
        'username': 'vmadmin',
        'secrets': {'private_key': private_key},
    })
    assert guest.status_code == 201, guest.text

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'guest-credential',
        'name': 'Guest credential',
        'deployment': {
            'name': 'guest-credential',
            'provider_id': provider['id'],
            'credentials_id': provider_credential['id'],
            'guest_credential_id': guest.json()['id'],
            'variables': {
                **deployment_payload['variables'],
                'name': 'guest-credential',
                'install_qemu_guest_agent': True,
                'cloud_init_snippet_storage': 'local',
            },
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'agent', 'type': 'wait_for_agent', 'depends_on': ['apply']},
        ],
    })
    assert created.status_code == 201, created.text

    monkeypatch.setattr(
        'app.api.automation.provider_for',
        lambda credential: type('Provider', (), {'ssh_preflight': lambda self: {'ok': True}})(),
    )

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['variables']['ssh_username'] == 'vmadmin'
    assert execution.json()['variables']['ssh_public_key'] == ' '.join(public_key.split()[:2])
    assert execution.json()['variables']['install_qemu_guest_agent'] is True
    assert execution.json()['variables']['cloud_init_snippet_storage'] == 'local'
    assert execution.json()['workflow']['blueprint']['guest_credential_id'] == guest.json()['id']

    protected = client.delete(f"/api/v1/credentials/{guest.json()['id']}", headers=headers)
    assert protected.status_code == 409


def test_blueprint_guest_credential_accepts_password_only_ssh_without_persisting_password(client, headers):
    from app.database import session
    from app.executors.terraform import guest_credential_runtime_variables
    from app.models import Deployment

    provider_credential, provider, deployment_payload = resources(client, headers)
    password = 'temporary-password-1234'
    guest = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Password-only VM SSH',
        'type': 'ssh',
        'endpoint': 'ssh://vm.example.com:22',
        'username': 'vmadmin',
        'secrets': {'password': password},
    })
    assert guest.status_code == 201, guest.text
    assert guest.json()['supports_cloud_init_password'] is True
    assert guest.json()['supports_cloud_init_ssh_key'] is False

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'password-only-guest',
        'name': 'Password-only guest',
        'deployment': {
            'name': 'password-only-guest',
            'provider_id': provider['id'],
            'credentials_id': provider_credential['id'],
            'guest_credential_id': guest.json()['id'],
            'variables': {
                **deployment_payload['variables'],
                'name': 'password-only-guest',
            },
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['variables']['ssh_username'] == 'vmadmin'
    assert execution.json()['variables']['ssh_public_key'] is None
    assert password not in execution.text
    assert execution.json()['workflow']['blueprint']['guest_credential_id'] == guest.json()['id']

    with session() as db:
        deployment = db.get(Deployment, execution.json()['id'])
        assert password not in str(deployment.variables)
        assert password not in str(deployment.workflow)
        runtime_variables, runtime_password = guest_credential_runtime_variables(deployment)
    assert runtime_variables['ssh_username'] == 'vmadmin'
    assert 'ssh_public_key' not in runtime_variables
    assert runtime_password == password


def test_blueprint_runtime_apmid_is_required_by_ui_and_applied_by_backend(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    saved = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['IAASTEAM', 'CRM'],
    })
    assert saved.status_code == 200, saved.text

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'runtime-apmid',
        'name': 'Runtime APMID',
        'deployment': {
            'name': 'runtime-apmid',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'apmid': 'LEO',
            'environment': 'dev',
            'select_apmid_on_execute': True,
            'variables': {
                **deployment_payload['variables'],
                'name': 'runtime-apmid',
                'tags': ['apmid-leo', 'env-dev', 'leo.dev'],
            },
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'apmid': 'IAASTEAM'},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['variables']['tags'] == [
        'apmid-iaasteam',
        'env-dev',
        'iaasteam.dev',
    ]

    invalid = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'apmid': 'NOT_CONFIGURED'},
    )
    assert invalid.status_code == 422
    assert 'not configured' in invalid.text


def test_blueprint_runtime_environment_updates_tags_and_hostname(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    saved = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['IAASTEAM'],
    })
    assert saved.status_code == 200, saved.text

    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Runtime environment',
        'pattern': '{location}-{environment}-{number}',
        'padding': 3,
    })
    assert scheme.status_code == 201, scheme.text

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'runtime-environment',
        'name': 'Runtime Environment',
        'deployment': {
            'name': '{{ hostname }}',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'hostname_scheme_id': scheme.json()['id'],
            'hostname_values': {'environment': 'dev'},
            'apmid': 'LEO',
            'environment': 'dev',
            'select_environment_on_execute': True,
            'variables': {
                **deployment_payload['variables'],
                'name': '{{ hostname }}',
                'tags': ['apmid-leo', 'env-dev', 'leo.dev'],
            },
        },
        'workflow': [
            {'id': 'hostname', 'type': 'generate_hostname'},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['hostname']},
        ],
    })
    assert created.status_code == 201, created.text

    missing = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert missing.status_code == 422
    assert 'Environment must be selected' in missing.text

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'environment': 'prod'},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['name'] == 'wro-prod-001'
    assert execution.json()['variables']['tags'] == [
        'apmid-leo',
        'env-prod',
        'leo.prod',
    ]

    disabled = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': False},
        'apmids': ['IAASTEAM'],
    })
    assert disabled.status_code == 200, disabled.text
    invalid = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'environment': 'prod'},
    )
    assert invalid.status_code == 422
    assert 'disabled or unavailable' in invalid.text


def test_blueprint_runtime_apmid_and_environment_can_be_selected_together(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    saved = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['IAASTEAM', 'CRM'],
    })
    assert saved.status_code == 200, saved.text

    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'runtime-classification',
        'name': 'Runtime Classification',
        'deployment': {
            'name': 'runtime-classification',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'apmid': 'LEO',
            'environment': 'test',
            'select_apmid_on_execute': True,
            'select_environment_on_execute': True,
            'variables': {
                **deployment_payload['variables'],
                'name': 'runtime-classification',
                'tags': ['apmid-leo', 'env-test', 'leo.test'],
            },
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'apmid': 'CRM', 'environment': 'nonprod'},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['variables']['tags'] == [
        'apmid-crm',
        'crm.nonprod',
        'env-nonprod',
    ]


def test_blueprint_fixed_apmid_cannot_be_overridden_at_runtime(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'fixed-apmid',
        'name': 'Fixed APMID',
        'deployment': {
            'name': 'fixed-apmid',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'apmid': 'CRM',
            'variables': {
                **deployment_payload['variables'],
                'name': 'fixed-apmid',
                'tags': ['env-prod'],
            },
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text

    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert execution.status_code == 202, execution.text
    assert execution.json()['variables']['tags'] == [
        'apmid-crm',
        'crm.prod',
        'env-prod',
    ]

    override = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={'apmid': 'IAASTEAM'},
    )
    assert override.status_code == 422
    assert 'does not allow changing APMID' in override.text


def test_blueprint_validates_dag_visibility_and_compiles_deployment(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    scheme = client.post('/api/v1/hostname-schemes', headers=headers, json={
        'name': 'Application hosts', 'pattern': '{location}-{env}-{role}-{number}', 'padding': 3,
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
        'variables': {'cpu': 4, 'memory': 8192}, 'hostname_values': {'env': 'prod'},
    })
    assert execution.status_code == 202, execution.text
    assert execution.json()['name'] == 'wro-prod-server-001'
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
            'name': 'manual-deployment-name',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'template': 'proxmox-vm',
            'hostname_scheme_id': scheme.json()['id'],
            'hostname_values': {'env': 'prod', 'role': 'web'},
            'variables': {
                'name': 'manual-vm-name',
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
    assert result['name'] == 'prod-server-001'
    assert result['variables']['name'] == 'prod-server-001'
    assert result['name'] != 'manual-deployment-name'
    assert result['variables']['name'] != 'manual-vm-name'
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


def test_blueprint_qemu_agent_requires_explicit_snippet_storage(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    variables = {
        **deployment_payload['variables'],
        'install_qemu_guest_agent': True,
    }
    variables.pop('cloud_init_snippet_storage', None)

    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'missing-snippet-storage',
        'name': 'Missing snippet storage',
        'deployment': {
            'name': 'missing-snippet-storage',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'template': 'proxmox-vm',
            'variables': variables,
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'agent', 'type': 'wait_for_agent', 'depends_on': ['apply']},
        ],
    })
    assert response.status_code == 422
    assert 'cloud_init_snippet_storage' in response.text


def test_blueprint_rejects_declarative_step_after_apply(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'bad-order',
        'name': 'Bad order',
        'deployment': {
            'name': 'bad-order',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'tags', 'type': 'set_tags', 'depends_on': ['apply']},
        ],
    })
    assert response.status_code == 422
    assert 'before terraform_apply' in response.text


def test_blueprint_rejects_release_ip_during_provisioning(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'unsafe-release-ip',
        'name': 'Unsafe release IP',
        'deployment': {
            'name': 'unsafe-release-ip',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'release', 'type': 'release_ip', 'depends_on': ['apply']},
        ],
    })
    assert response.status_code == 422
    assert 'release_ip is not allowed' in response.text


def test_blueprint_allows_destroy_only_as_rollback_target(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    invalid = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'destroy-normal-flow',
        'name': 'Destroy normal flow',
        'deployment': {
            'name': 'destroy-normal-flow',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'destroy', 'type': 'terraform_destroy', 'depends_on': ['apply']},
        ],
    })
    assert invalid.status_code == 422
    assert 'rollback target' in invalid.text

    valid = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'destroy-rollback',
        'name': 'Destroy rollback',
        'deployment': {
            'name': 'destroy-rollback',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {'id': 'rollback_destroy', 'type': 'terraform_destroy'},
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'rollback': 'rollback_destroy'},
        ],
    })
    assert valid.status_code == 201, valid.text


def test_blueprint_execution_rejects_qemu_agent_when_ssh_preflight_fails(client, headers, monkeypatch):
    credential, provider, deployment_payload = resources(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'ssh-preflight-failure',
        'name': 'SSH preflight failure',
        'deployment': {
            'name': 'ssh-preflight-failure',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': {
                **deployment_payload['variables'],
                'install_qemu_guest_agent': True,
                'cloud_init_snippet_storage': 'local',
            },
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'agent', 'type': 'wait_for_agent', 'depends_on': ['apply']},
        ],
    })
    assert created.status_code == 201, created.text

    monkeypatch.setattr(
        'app.api.automation.provider_for',
        lambda credential: type('Provider', (), {
            'ssh_preflight': lambda self: {'ok': False, 'reason': 'ssh_unreachable'}
        })(),
    )
    execution = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=key(headers),
        json={},
    )
    assert execution.status_code == 409
    assert 'ssh_unreachable' in execution.text


def test_blueprint_rejects_runtime_controls_on_legacy_markers(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'legacy-marker-condition',
        'name': 'Legacy marker condition',
        'deployment': {
            'name': 'legacy-marker-condition',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [
            {
                'id': 'clone',
                'type': 'clone_vm',
                'conditions': {'environment': 'prod'},
            },
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        ],
    })
    assert response.status_code == 422
    assert 'cannot use conditions, retry or rollback' in response.text


def test_blueprint_requires_exactly_one_apply_and_vm_steps_depend_on_it(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    base = {
        'name': 'Workflow validation',
        'deployment': {
            'name': 'workflow-validation',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
    }

    missing_apply = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'slug': 'missing-apply',
        'workflow': [{'id': 'vm', 'type': 'wait_for_vm'}],
    })
    assert missing_apply.status_code == 422
    assert 'terraform_apply or a legacy provisioning marker' in missing_apply.text

    duplicate_apply = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'slug': 'duplicate-apply',
        'workflow': [
            {'id': 'apply1', 'type': 'terraform_apply'},
            {'id': 'apply2', 'type': 'terraform_apply'},
        ],
    })
    assert duplicate_apply.status_code == 422
    assert 'exactly one terraform_apply' in duplicate_apply.text

    vm_before_apply = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'slug': 'vm-before-apply',
        'workflow': [
            {'id': 'vm', 'type': 'wait_for_vm'},
            {'id': 'apply', 'type': 'terraform_apply'},
        ],
    })
    assert vm_before_apply.status_code == 422
    assert 'must depend on terraform_apply' in vm_before_apply.text

    plan_after_apply = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'slug': 'plan-after-apply',
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'plan', 'type': 'terraform_plan', 'depends_on': ['apply']},
        ],
    })
    assert plan_after_apply.status_code == 422
    assert 'terraform_plan must be an ancestor' in plan_after_apply.text


def test_blueprint_rejects_proxmox_only_steps_for_aws(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'AWS Blueprint',
        'type': 'aws',
        'secrets': {
            'access_key_id': 'AKIAEXAMPLEVALUE',
            'secret_access_key': 'example-secret-key-material',
        },
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'AWS Blueprint',
        'type': 'aws',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text

    response = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'aws-invalid-waits',
        'name': 'AWS invalid waits',
        'deployment': {
            'name': 'aws-invalid-waits',
            'provider_id': provider.json()['id'],
            'credentials_id': credential.json()['id'],
            'template': 'aws-ec2',
            'variables': {
                'name': 'aws-invalid-waits',
                'region': 'eu-central-1',
                'ami': 'ami-1234567890abcdef0',
                'instance_type': 't3.micro',
                'subnet_id': 'subnet-1234567890abcdef0',
                'security_group_ids': ['sg-1234567890abcdef0'],
            },
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'vm', 'type': 'wait_for_vm', 'depends_on': ['apply']},
            {'id': 'snapshot', 'type': 'create_snapshot', 'depends_on': ['vm']},
        ],
    })
    assert response.status_code == 422
    assert 'supported only for Proxmox' in response.text
    assert 'create_snapshot' in response.text
    assert 'wait_for_vm' in response.text


def test_existing_legacy_blueprint_can_be_saved_without_explicit_apply(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'legacy-editable',
        'name': 'Legacy Editable',
        'deployment': {
            'name': 'legacy-editable',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [{'id': 'apply', 'type': 'terraform_apply'}],
    })
    assert created.status_code == 201, created.text

    update = client.put('/api/v1/blueprints/' + str(created.json()['id']), headers=headers, json={
        'slug': 'legacy-editable',
        'name': 'Legacy Editable',
        'deployment': {
            'name': 'legacy-editable',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [{'id': 'clone', 'type': 'clone_vm'}],
    })
    assert update.status_code == 200, update.text
    assert update.json()['workflow'] == [{'id': 'clone', 'type': 'clone_vm', 'depends_on': [], 'conditions': {}, 'retry': 0, 'timeout': 600, 'rollback': None}]

    new_legacy = client.post('/api/v1/blueprints', headers=headers, json={
        'slug': 'legacy-new-blocked',
        'name': 'Legacy New Blocked',
        'deployment': {
            'name': 'legacy-new-blocked',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
        'workflow': [{'id': 'clone', 'type': 'clone_vm'}],
    })
    assert new_legacy.status_code == 422
    assert 'explicit terraform_apply' in new_legacy.text


def test_blueprint_approval_step_must_be_between_plan_and_apply(client, headers):
    credential, provider, deployment_payload = resources(client, headers)
    base = {
        'slug': 'approval-order',
        'name': 'Approval Order',
        'requires_approval': True,
        'deployment': {
            'name': 'approval-order',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': deployment_payload['variables'],
        },
    }

    after_apply = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
            {'id': 'approval', 'type': 'approval', 'depends_on': ['apply']},
        ],
    })
    assert after_apply.status_code == 422
    assert 'approval must be an ancestor of terraform_apply' in after_apply.text

    approval_before_plan = client.post('/api/v1/blueprints', headers=headers, json={
        **base,
        'slug': 'approval-before-plan',
        'workflow': [
            {'id': 'approval', 'type': 'approval'},
            {'id': 'plan', 'type': 'terraform_plan', 'depends_on': ['approval']},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['plan']},
        ],
    })
    assert approval_before_plan.status_code == 422
    assert 'terraform_plan must be an ancestor of approval' in approval_before_plan.text
