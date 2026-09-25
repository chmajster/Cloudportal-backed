import pytest

from app.awx import AwxClient, AwxError, normalize_awx_endpoint


def test_normalize_awx_endpoint_accepts_host_and_known_api_paths():
    assert normalize_awx_endpoint('awx.example.com') == 'https://awx.example.com'
    assert normalize_awx_endpoint('http://awx.example.com:8080/') == 'http://awx.example.com:8080'
    assert normalize_awx_endpoint('https://awx.example.com/api/v2/') == 'https://awx.example.com'
    assert normalize_awx_endpoint('https://aap.example.com/api/controller/v2') == 'https://aap.example.com'


@pytest.mark.parametrize('value', [
    '',
    'ftp://awx.example.com',
    'https://admin:secret@awx.example.com',
    'https://awx.example.com/?token=secret',
])
def test_normalize_awx_endpoint_rejects_unsafe_values(value):
    with pytest.raises(AwxError):
        normalize_awx_endpoint(value)


class FakeAwxClient(AwxClient):
    def __init__(self):
        self.seen = {}
        self.groups = []
        self.memberships = []

    def ensure_inventory(self, *, inventory_id=None, name='CloudPortal', organization_id=None):
        self.seen['inventory'] = {
            'inventory_id': inventory_id,
            'name': name,
            'organization_id': organization_id,
        }
        return {'id': inventory_id or 42, 'name': name}

    def ensure_host(self, *, inventory_id, hostname, ansible_host, variables):
        self.seen['host'] = {
            'inventory_id': inventory_id,
            'hostname': hostname,
            'ansible_host': ansible_host,
            'variables': variables,
        }
        return {'id': 101, 'name': hostname}

    def ensure_group(self, *, inventory_id, name):
        self.groups.append((inventory_id, name))
        return {'id': 200 + len(self.groups), 'name': name}

    def add_host_to_group(self, *, group_id, host_id):
        self.memberships.append((group_id, host_id))


def test_register_host_is_metadata_driven_and_groups_are_automatic():
    client = FakeAwxClient()

    result = client.register_host(
        hostname='srv001',
        ansible_host='10.20.30.40',
        deployment_id='dep-123',
        environment='Prod',
        apmid='LEO',
        inventory_name='CloudPortal',
        organization_id=7,
    )

    assert result == {
        'inventory_id': 42,
        'inventory_name': 'CloudPortal',
        'host_id': 101,
        'host_name': 'srv001',
        'groups': ['env-prod', 'apmid-leo'],
    }
    assert client.seen['inventory'] == {
        'inventory_id': None,
        'name': 'CloudPortal',
        'organization_id': 7,
    }
    assert client.seen['host']['variables'] == {
        'ansible_host': '10.20.30.40',
        'cloudportal_managed': True,
        'cloudportal_deployment_id': 'dep-123',
        'environment': 'Prod',
        'apmid': 'LEO',
    }
    assert client.groups == [(42, 'env-prod'), (42, 'apmid-leo')]
    assert client.memberships == [(201, 101), (202, 101)]



class FakeLaunchAwxClient(AwxClient):
    def __init__(self):
        self.payload = None

    def request(self, method, path, **kwargs):
        assert method == 'POST'
        assert path == 'job_templates/33/launch/'
        self.payload = kwargs['json']

        class Response:
            @staticmethod
            def json():
                return {'job': 444}

        return Response()


def test_launch_job_template_receives_runtime_environment_and_apmid():
    client = FakeLaunchAwxClient()

    result = client.launch_job_template(
        33,
        hostname='srv001',
        deployment_id='dep-123',
        environment='NonProd',
        apmid='leo',
    )

    assert result == {'job': 444}
    assert client.payload == {
        'limit': 'srv001',
        'extra_vars': {
            'cloudportal_onboarding': True,
            'cloudportal_deployment_id': 'dep-123',
            'environment': 'nonprod',
            'apmid': 'LEO',
        },
    }


class FakeRemovalAwxClient(AwxClient):
    def __init__(self):
        self.deleted = []

    def list_resource(self, resource, *, params=None):
        if resource == 'inventories':
            return [{'id': 42, 'name': 'CloudPortal'}]
        if resource == 'hosts':
            return [
                {'id': 101, 'name': 'srv001'},
                {'id': 102, 'name': 'another-host'},
            ]
        return []

    def request(self, method, path, **kwargs):
        assert method == 'DELETE'
        self.deleted.append(path)

        class Response:
            @staticmethod
            def json():
                return {}

        return Response()


def test_remove_host_deletes_only_matching_host_without_creating_inventory():
    client = FakeRemovalAwxClient()

    result = client.remove_host(hostname='srv001', inventory_name='CloudPortal')

    assert result == {
        'removed': True,
        'host_ids': [101],
        'inventory_id': 42,
    }
    assert client.deleted == ['hosts/101/']


class FakeDiscoveryAwxClient(AwxClient):
    def __init__(self):
        self.endpoint = 'https://awx.example.com'
        self.username = 'admin'

    def api_base(self):
        return self.endpoint + '/api/v2'

    def current_user(self):
        return {'id': 1, 'username': 'admin'}

    def request(self, method, path, **kwargs):
        assert method == 'GET'
        assert path == 'ping/'

        class Response:
            @staticmethod
            def json():
                return {'version': '24.6.1'}

        return Response()

    def list_resource(self, resource, *, params=None):
        return {
            'organizations': [{'id': 7, 'name': 'Platform'}],
            'projects': [{
                'id': 21,
                'name': 'Linux Automation',
                'organization': 7,
                'status': 'successful',
                'scm_type': 'git',
            }],
            'inventories': [{'id': 12, 'name': 'Linux Servers', 'organization': 7}],
            'job_templates': [{
                'id': 33,
                'name': 'Initial Linux',
                'inventory': 12,
                'project': 21,
                'playbook': 'initial.yml',
                'execution_environment': 4,
            }],
        }.get(resource, [])


def test_discovery_exposes_awx_projects_and_job_template_relationships():
    result = FakeDiscoveryAwxClient().discovery()

    assert result['projects'] == [{
        'id': 21,
        'name': 'Linux Automation',
        'organization': 7,
        'status': 'successful',
        'scm_type': 'git',
    }]
    assert result['job_templates'] == [{
        'id': 33,
        'name': 'Initial Linux',
        'inventory': 12,
        'project': 21,
        'playbook': 'initial.yml',
        'execution_environment': 4,
    }]


class FakePagedAwxClient(AwxClient):
    def __init__(self):
        self.pages = []

    def request(self, method, path, **kwargs):
        page = int((kwargs.get('params') or {}).get('page') or 1)
        self.pages.append(page)

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        if page == 1:
            return Response({'results': [{'id': 1}], 'next': '/api/v2/hosts/?page=2'})
        return Response({'results': [{'id': 2}], 'next': None})


def test_list_resource_follows_awx_pagination():
    client = FakePagedAwxClient()
    assert client.list_resource('hosts') == [{'id': 1}, {'id': 2}]
    assert client.pages == [1, 2]


class FakeExistingHostAwxClient(AwxClient):
    def __init__(self):
        self.payload = None

    def list_resource(self, resource, *, params=None):
        assert resource == 'hosts'
        return [{
            'id': 101,
            'name': 'srv001',
            'variables': 'owner: platform\ncustom_flag: true\nansible_host: 10.0.0.1\n',
        }]

    def request(self, method, path, **kwargs):
        assert method == 'PATCH'
        assert path == 'hosts/101/'
        self.payload = kwargs['json']

        class Response:
            @staticmethod
            def json():
                return {'id': 101, 'name': 'srv001'}

        return Response()


def test_ensure_host_preserves_non_cloudportal_variables():
    client = FakeExistingHostAwxClient()

    client.ensure_host(
        inventory_id=42,
        hostname='srv001',
        ansible_host='10.20.30.40',
        variables={
            'ansible_host': '10.20.30.40',
            'cloudportal_managed': True,
            'cloudportal_deployment_id': 'dep-123',
        },
    )

    import json
    variables = json.loads(client.payload['variables'])
    assert variables['owner'] == 'platform'
    assert variables['custom_flag'] is True
    assert variables['ansible_host'] == '10.20.30.40'
    assert variables['cloudportal_deployment_id'] == 'dep-123'
