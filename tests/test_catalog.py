def test_manifest_catalog_exposes_all_approved_templates(client, headers):
    response = client.get('/api/v1/templates', headers=headers)
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['items']}
    assert {'proxmox-vm', 'aws-ec2', 'azure-linux-vm', 'openstack-vm', 'vmware-vsphere-vm'} <= set(items)
    assert items['aws-ec2']['provider'] == 'aws'
    assert 'security_group_ids' in items['aws-ec2']['variables_schema']['properties']
    assert items['proxmox-vm']['version'] == 6
    proxmox_properties = items['proxmox-vm']['variables_schema']['properties']
    assert {'tags', 'dns_servers', 'dns_domain', 'install_qemu_guest_agent', 'cloud_init_snippet_storage'} <= set(proxmox_properties)

    source = client.get('/api/v1/templates/proxmox-vm/source', headers=headers)
    assert source.status_code == 200, source.text
    source_data = source.json()
    assert source_data['id'] == 'proxmox-vm'
    assert source_data['files']
    assert {'main.tf', 'variables.tf'} <= {item['name'] for item in source_data['files']}
    assert all(item['name'].endswith('.tf') for item in source_data['files'])
    assert all(isinstance(item['content'], str) for item in source_data['files'])
    terraform_source = '\n'.join(item['content'] for item in source_data['files'])
    assert 'proxmox_virtual_environment_file' in terraform_source
    assert 'qemu_guest_agent_cloud_init' in terraform_source
    assert 'vendor_data_file_id' in terraform_source
    assert 'qemu-guest-agent' in terraform_source
    assert 'install_qemu_guest_agent' in terraform_source
    assert 'cloud_init_snippet_storage' in terraform_source
    assert 'qemu_guest_agent_bootstrap' in terraform_source
    assert 'bootstrap_username' in terraform_source
    assert 'bootstrap_public_key' in terraform_source
    assert 'output "primary_ip"' in terraform_source
    assert 'value = local.configured_primary_ip' in terraform_source
    assert 'ipv4_addresses' not in terraform_source
    # Version 6 adds API-uploaded NoCloud media without removing the legacy path.
    assert 'resource "proxmox_virtual_environment_file" "cloud_init_seed"' in terraform_source
    assert 'content_type = "iso"' in terraform_source
    assert 'proxmox_virtual_environment_file.cloud_init_seed[0].id' in terraform_source
    assert 'variable "cloud_init_seed_checksum"' in terraform_source

    playbooks = client.get('/api/v1/ansible/playbooks', headers=headers)
    assert playbooks.status_code == 200
    playbook_items = {item['id']: item for item in playbooks.json()['items']}
    ids = set(playbook_items)
    assert {
        'bootstrap-linux', 'validate-linux', 'linux-system-update', 'linux-install-packages',
        'linux-remove-packages', 'linux-reboot', 'linux-service', 'linux-set-hostname',
        'linux-set-timezone', 'linux-qemu-guest-agent', 'linux-nginx', 'linux-webserver',
        'linux-chrony', 'linux-create-user', 'linux-disk-usage', 'linux-system-info',
        'linux-cleanup', 'validate-windows', 'windows-update', 'windows-reboot',
        'windows-service', 'windows-set-hostname', 'windows-system-info',
    } <= ids
    assert len(ids) >= 23
    assert playbook_items['linux-system-update']['category'] == 'Aktualizacje'
    assert playbook_items['linux-system-update']['description']
    assert {'service_name', 'service_state', 'service_enabled'} <= set(playbook_items['linux-service']['variables'])
    assert {'service_name', 'service_state', 'service_enabled'} <= set(playbook_items['linux-service']['required_variables'])

    playbook_source = client.get('/api/v1/ansible/playbooks/bootstrap-linux/source', headers=headers)
    assert playbook_source.status_code == 200, playbook_source.text
    playbook_data = playbook_source.json()
    assert playbook_data['id'] == 'bootstrap-linux'
    assert playbook_data['files']
    assert all(item['name'].endswith(('.yml', '.yaml')) for item in playbook_data['files'])
    assert all(item['role'] in {'main', 'wait', 'validate'} for item in playbook_data['files'])
    assert all(isinstance(item['content'], str) for item in playbook_data['files'])


def test_catalog_items_can_be_disabled_and_block_new_use(client, headers):
    import uuid

    templates = client.get('/api/v1/templates', headers=headers)
    assert templates.status_code == 200
    assert all(item['enabled'] is True for item in templates.json()['items'])

    playbooks = client.get('/api/v1/ansible/playbooks', headers=headers)
    assert playbooks.status_code == 200
    assert all(item['enabled'] is True for item in playbooks.json()['items'])

    disabled_template = client.put(
        '/api/v1/catalog/templates/aws-ec2/enabled',
        headers=headers,
        json={'enabled': False},
    )
    assert disabled_template.status_code == 200, disabled_template.text
    assert disabled_template.json()['enabled'] is False

    disabled_playbook = client.put(
        '/api/v1/catalog/playbooks/validate-linux/enabled',
        headers=headers,
        json={'enabled': False},
    )
    assert disabled_playbook.status_code == 200, disabled_playbook.text
    assert disabled_playbook.json()['enabled'] is False

    templates = {item['id']: item for item in client.get('/api/v1/templates', headers=headers).json()['items']}
    playbooks = {item['id']: item for item in client.get('/api/v1/ansible/playbooks', headers=headers).json()['items']}
    assert templates['aws-ec2']['enabled'] is False
    assert playbooks['validate-linux']['enabled'] is False

    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'AWS disabled template test',
        'type': 'aws',
        'secrets': {
            'access_key_id': 'AKIAEXAMPLEVALUE',
            'secret_access_key': 'example-secret-key-material',
        },
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'AWS disabled template test',
        'type': 'aws',
        'credentials_id': credential['id'],
    }).json()

    deployment = client.post('/api/v1/deployments', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'name': 'disabled-template',
        'provider_id': provider['id'],
        'template': 'aws-ec2',
        'credentials_id': credential['id'],
        'variables': {
            'name': 'disabled-template',
            'region': 'eu-central-1',
            'ami': 'ami-1234567890abcdef0',
            'instance_type': 't3.micro',
            'subnet_id': 'subnet-1234567890abcdef0',
            'security_group_ids': ['sg-1234567890abcdef0'],
        },
    })
    assert deployment.status_code == 409
    assert 'disabled' in deployment.text

    ssh = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'SSH disabled playbook test',
        'type': 'ssh',
        'endpoint': 'ssh://192.0.2.1:22',
        'username': 'clouduser',
        'secrets': {
            'private_key': 'private-key-data',
            'known_hosts': '192.0.2.1 ssh-ed25519 AAAATEST',
        },
    })
    assert ssh.status_code == 201, ssh.text

    job = client.post('/api/v1/jobs', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'operation': 'ansible.execute',
        'ansible': {
            'playbook': 'validate-linux',
            'credentials_id': ssh.json()['id'],
            'inventory': {'hosts': ['192.0.2.1']},
            'variables': {},
        },
    })
    assert job.status_code == 409
    assert 'disabled' in job.text

    assert client.put('/api/v1/catalog/templates/aws-ec2/enabled', headers=headers, json={'enabled': True}).json()['enabled'] is True
    assert client.put('/api/v1/catalog/playbooks/validate-linux/enabled', headers=headers, json={'enabled': True}).json()['enabled'] is True


def test_aws_provider_and_template_are_validated_before_job_creation(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'AWS Terraform',
        'type': 'aws',
        'secrets': {
            'access_key_id': 'AKIAEXAMPLEVALUE',
            'secret_access_key': 'example-secret-key-material',
        },
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'AWS',
        'type': 'aws',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text

    import uuid
    response = client.post('/api/v1/deployments', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'name': 'aws-test',
        'provider_id': provider.json()['id'],
        'template': 'aws-ec2',
        'credentials_id': credential.json()['id'],
        'variables': {
            'name': 'aws-test',
            'region': 'eu-central-1',
            'ami': 'ami-1234567890abcdef0',
            'instance_type': 't3.micro',
            'subnet_id': 'subnet-1234567890abcdef0',
            'security_group_ids': ['sg-1234567890abcdef0'],
        },
    })
    assert response.status_code == 202, response.text
    assert response.json()['provider'] == 'aws'

    invalid = client.post('/api/v1/deployments', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'name': 'invalid-aws',
        'provider_id': provider.json()['id'],
        'template': 'aws-ec2',
        'credentials_id': credential.json()['id'],
        'variables': {
            'name': 'invalid-aws',
            'region': 'eu-central-1',
            'ami': 'ami-1234567890abcdef0',
            'subnet_id': 'subnet-1234567890abcdef0',
            'security_group_ids': ['not-a-security-group'],
        },
    })
    assert invalid.status_code == 422


def test_template_provider_mismatch_is_rejected(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'AWS mismatch',
        'type': 'aws',
        'secrets': {
            'access_key_id': 'AKIAEXAMPLEVALUE',
            'secret_access_key': 'example-secret-key-material',
        },
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'AWS mismatch',
        'type': 'aws',
        'credentials_id': credential['id'],
    }).json()

    import uuid
    response = client.post('/api/v1/deployments', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'name': 'mismatch',
        'provider_id': provider['id'],
        'template': 'proxmox-vm',
        'credentials_id': credential['id'],
        'variables': {
            'name': 'mismatch',
            'node': 'pve01',
            'template_id': 9000,
            'storage': 'local-lvm',
        },
    })
    assert response.status_code == 422



def _custom_playbook_payload(playbook_id='custom-hello', message='version-one'):
    return {
        'id': playbook_id,
        'name': 'Custom Hello',
        'description': 'Playbook dodany przez API Cloudportal.',
        'category': 'Własne',
        'transport': 'ssh',
        'variables': {
            'custom_message': {
                'required': True,
                'pattern': '[A-Za-z0-9 _.-]{1,100}',
            },
        },
        'wait_for_connection': False,
        'validate_after': False,
        'content': (
            '- name: Custom playbook\n'
            '  hosts: all\n'
            '  gather_facts: false\n'
            '  tasks:\n'
            '    - name: Show custom message\n'
            '      ansible.builtin.debug:\n'
            f'        msg: "{message} {{{{ custom_message }}}}"\n'
        ),
    }


def test_custom_ansible_playbook_crud_and_catalog(client, headers):
    created = client.post(
        '/api/v1/ansible/custom-playbooks',
        headers=headers,
        json=_custom_playbook_payload(),
    )
    assert created.status_code == 201, created.text
    assert created.json()['id'] == 'custom-hello'
    assert created.json()['custom'] is True
    assert created.json()['version'] == 1
    assert created.json()['enabled'] is True

    listed = client.get('/api/v1/ansible/playbooks', headers=headers)
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()['items'] if row['id'] == 'custom-hello')
    assert item['custom'] is True
    assert item['required_variables'] == ['custom_message']

    detail = client.get('/api/v1/ansible/custom-playbooks/custom-hello', headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()['variables']['custom_message']['required'] is True
    assert 'version-one' in detail.json()['content']

    source = client.get('/api/v1/ansible/playbooks/custom-hello/source', headers=headers)
    assert source.status_code == 200, source.text
    main = next(row for row in source.json()['files'] if row['role'] == 'main')
    assert main['name'] == 'custom-custom-hello.yml'
    assert 'version-one' in main['content']
    assert source.json()['custom'] is True

    updated_payload = _custom_playbook_payload(message='version-two')
    updated_payload['name'] = 'Custom Hello v2'
    updated = client.put(
        '/api/v1/ansible/custom-playbooks/custom-hello',
        headers=headers,
        json=updated_payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['version'] == 2
    assert updated.json()['name'] == 'Custom Hello v2'

    disabled = client.put(
        '/api/v1/ansible/custom-playbooks/custom-hello/enabled',
        headers=headers,
        json={'enabled': False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()['enabled'] is False

    enabled = client.put(
        '/api/v1/ansible/custom-playbooks/custom-hello/enabled',
        headers=headers,
        json={'enabled': True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()['enabled'] is True

    deleted = client.delete('/api/v1/ansible/custom-playbooks/custom-hello', headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['deleted'] is True
    assert client.get('/api/v1/ansible/custom-playbooks/custom-hello', headers=headers).status_code == 404


def test_custom_ansible_playbook_blocks_controller_side_execution(client, headers):
    delegated = _custom_playbook_payload('custom-delegated')
    delegated['content'] = (
        '- name: Unsafe\n'
        '  hosts: all\n'
        '  tasks:\n'
        '    - name: Local execution\n'
        '      delegate_to: localhost\n'
        '      ansible.builtin.command: id\n'
    )
    response = client.post('/api/v1/ansible/custom-playbooks', headers=headers, json=delegated)
    assert response.status_code == 422, response.text
    assert 'controller-side' in response.text

    lookup = _custom_playbook_payload('custom-lookup')
    lookup['content'] = (
        '- name: Unsafe lookup\n'
        '  hosts: all\n'
        '  tasks:\n'
        '    - ansible.builtin.debug:\n'
        '        msg: "{{ lookup(\'pipe\', \'id\') }}"\n'
    )
    response = client.post('/api/v1/ansible/custom-playbooks', headers=headers, json=lookup)
    assert response.status_code == 422, response.text
    assert 'lookup' in response.text.lower()

    legacy_loop = _custom_playbook_payload('custom-with-pipe')
    legacy_loop['content'] = (
        '- name: Unsafe legacy lookup\n'
        '  hosts: all\n'
        '  tasks:\n'
        '    - ansible.builtin.debug:\n'
        '        msg: "{{ item }}"\n'
        '      with_pipe: id\n'
    )
    response = client.post('/api/v1/ansible/custom-playbooks', headers=headers, json=legacy_loop)
    assert response.status_code == 422, response.text
    assert 'with_pipe' in response.text

    action_shorthand = _custom_playbook_payload('custom-action-shorthand')
    action_shorthand['content'] = (
        '- name: Unsafe action shorthand\n'
        '  hosts: all\n'
        '  tasks:\n'
        '    - name: Read controller file\n'
        '      action: ansible.builtin.fetch src=/etc/cloudportal-backed/master.key dest=/tmp/key\n'
    )
    response = client.post('/api/v1/ansible/custom-playbooks', headers=headers, json=action_shorthand)
    assert response.status_code == 422, response.text
    assert 'action' in response.text.lower()


def test_custom_ansible_playbook_requires_manage_permission(client, headers):
    from conftest import new_user

    _user, read_only = new_user(
        client,
        headers,
        username='ansible-reader',
        permissions=['ansible.read'],
    )
    response = client.post(
        '/api/v1/ansible/custom-playbooks',
        headers=read_only,
        json=_custom_playbook_payload('custom-denied'),
    )
    assert response.status_code == 403

    created = client.post(
        '/api/v1/ansible/custom-playbooks',
        headers=headers,
        json=_custom_playbook_payload('custom-state-rbac'),
    )
    assert created.status_code == 201, created.text

    _settings_user, settings_only = new_user(
        client,
        headers,
        username='settings-only-playbook-user',
        permissions=['settings.update', 'ansible.read'],
    )
    legacy_state = client.put(
        '/api/v1/catalog/playbooks/custom-state-rbac/enabled',
        headers=settings_only,
        json={'enabled': False},
    )
    assert legacy_state.status_code == 403, legacy_state.text


def test_custom_ansible_job_uses_queued_version_snapshot(client, headers, monkeypatch):
    import uuid
    from pathlib import Path

    from app.jobs.worker import execute

    created = client.post(
        '/api/v1/ansible/custom-playbooks',
        headers=headers,
        json=_custom_playbook_payload('custom-snapshot', 'queued-version'),
    )
    assert created.status_code == 201, created.text

    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Custom playbook SSH',
        'type': 'ssh',
        'endpoint': 'ssh://192.0.2.80:22',
        'username': 'clouduser',
        'secrets': {'password': 'custom-playbook-password'},
    })
    assert credential.status_code == 201, credential.text

    queued = client.post('/api/v1/jobs', headers={
        **headers,
        'Idempotency-Key': str(uuid.uuid4()),
    }, json={
        'operation': 'ansible.execute',
        'ansible': {
            'playbook': 'custom-snapshot',
            'credentials_id': credential.json()['id'],
            'inventory': {'hosts': ['192.0.2.80']},
            'variables': {'custom_message': 'hello'},
        },
    })
    assert queued.status_code == 202, queued.text

    changed = client.put(
        '/api/v1/ansible/custom-playbooks/custom-snapshot',
        headers=headers,
        json=_custom_playbook_payload('custom-snapshot', 'newer-version'),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()['version'] == 2

    observed = []

    def fake_run_process(argv, cwd, env, context, secrets=()):
        observed.append(Path(argv[3]).read_text(encoding='utf-8'))
        return ''

    monkeypatch.setattr('app.executors.ansible.run_process', fake_run_process)
    execute(queued.json()['id'])

    result = client.get('/api/v1/jobs/' + queued.json()['id'], headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'successful'
    assert len(observed) == 1
    assert 'queued-version' in observed[0]
    assert 'newer-version' not in observed[0]
