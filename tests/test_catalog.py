def test_manifest_catalog_exposes_all_approved_templates(client, headers):
    response = client.get('/api/v1/templates', headers=headers)
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['items']}
    assert {'proxmox-vm', 'aws-ec2', 'azure-linux-vm', 'openstack-vm', 'vmware-vsphere-vm'} <= set(items)
    assert items['aws-ec2']['provider'] == 'aws'
    assert 'security_group_ids' in items['aws-ec2']['variables_schema']['properties']
    assert items['proxmox-vm']['version'] == 2
    proxmox_properties = items['proxmox-vm']['variables_schema']['properties']
    assert {'tags', 'dns_servers', 'dns_domain'} <= set(proxmox_properties)

    playbooks = client.get('/api/v1/ansible/playbooks', headers=headers)
    assert playbooks.status_code == 200
    ids = {item['id'] for item in playbooks.json()['items']}
    assert {'bootstrap-linux', 'validate-linux', 'validate-windows'} <= ids


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
