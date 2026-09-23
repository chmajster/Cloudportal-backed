def test_manifest_catalog_exposes_all_approved_templates(client, headers):
    response = client.get('/api/v1/templates', headers=headers)
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['items']}
    assert {'proxmox-vm', 'aws-ec2', 'azure-linux-vm', 'openstack-vm', 'vmware-vsphere-vm'} <= set(items)
    assert items['aws-ec2']['provider'] == 'aws'
    assert 'security_group_ids' in items['aws-ec2']['variables_schema']['properties']
    assert items['proxmox-vm']['version'] == 5
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
