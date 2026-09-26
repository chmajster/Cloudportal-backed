import pytest

from app.awx import (AwxClient, AwxError, DEFAULT_AWX_INVENTORY_PATTERN,
                     normalize_awx_endpoint, render_awx_inventory_name)


def test_inventory_pattern_defaults_to_project_apmid_environment():
    assert DEFAULT_AWX_INVENTORY_PATTERN == '<Projekt>-<APMID>-<ENV>'
    assert render_awx_inventory_name(
        DEFAULT_AWX_INVENTORY_PATTERN,
        project='Linux Automation',
        apmid='leo',
        environment='prod',
    ) == 'Linux Automation-LEO-PROD'
    assert render_awx_inventory_name(
        'static-inventory',
        project=None,
        apmid=None,
        environment=None,
    ) == 'static-inventory'


def test_inventory_pattern_rejects_missing_or_unknown_tokens():
    with pytest.raises(AwxError, match='requires values'):
        render_awx_inventory_name(
            '<Projekt>-<APMID>-<ENV>',
            project='Linux Automation',
            apmid='LEO',
            environment=None,
        )
    with pytest.raises(AwxError, match='Unsupported'):
        render_awx_inventory_name(
            '<Projekt>-<REGION>',
            project='Linux Automation',
            apmid='LEO',
            environment='prod',
        )


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



class FakeScopeMappingAwxClient(AwxClient):
    def __init__(self, *, organizations=None, projects=None):
        self.organizations = list(organizations or [])
        self.projects = list(projects or [])
        self.created = []

    def list_resource(self, resource, *, params=None):
        params = dict(params or {})
        if resource == 'organizations':
            rows = self.organizations
        elif resource == 'projects':
            rows = self.projects
        else:
            return []
        if params.get('name') is not None:
            rows = [row for row in rows if row.get('name') == params['name']]
        if params.get('organization') is not None:
            rows = [row for row in rows if int(row.get('organization') or 0) == int(params['organization'])]
        return list(rows)

    def request(self, method, path, **kwargs):
        assert method == 'POST'
        assert path == 'organizations/'
        payload = dict(kwargs['json'])
        self.created.append(payload)

        class Response:
            @staticmethod
            def json():
                return {'id': 7, 'name': payload['name']}

        return Response()


def test_awx_scope_mapping_creates_tenant_organization_when_missing():
    client = FakeScopeMappingAwxClient()

    organization = client.ensure_organization(name='Acme')

    assert organization == {'id': 7, 'name': 'Acme'}
    assert client.created == [{
        'name': 'Acme',
        'description': 'Managed automatically from CloudPortal Tenant',
    }]


def test_awx_scope_mapping_reuses_existing_tenant_organization():
    client = FakeScopeMappingAwxClient(
        organizations=[{'id': 7, 'name': 'Acme'}],
    )

    organization = client.ensure_organization(name='Acme')

    assert organization == {'id': 7, 'name': 'Acme'}
    assert client.created == []


def test_awx_scope_mapping_reuses_case_insensitive_names():
    client = FakeScopeMappingAwxClient(
        organizations=[{'id': 7, 'name': 'ACME'}],
        projects=[{'id': 21, 'name': 'PAYMENTS', 'organization': 7}],
    )

    organization = client.ensure_organization(name='Acme')
    project = client.project_for_organization(name='Payments', organization_id=7)

    assert organization['id'] == 7
    assert project['id'] == 21
    assert client.created == []


def test_awx_scope_mapping_resolves_project_only_inside_mapped_organization():
    client = FakeScopeMappingAwxClient(
        projects=[
            {'id': 20, 'name': 'Payments', 'organization': 8},
            {'id': 21, 'name': 'Payments', 'organization': 7, 'scm_type': 'git'},
        ],
    )

    project = client.project_for_organization(name='Payments', organization_id=7)

    assert project['id'] == 21
    assert project['organization'] == 7


def test_awx_scope_mapping_rejects_missing_project():
    client = FakeScopeMappingAwxClient(
        projects=[{'id': 20, 'name': 'Payments', 'organization': 8}],
    )

    with pytest.raises(AwxError, match='does not exist in mapped organization'):
        client.project_for_organization(name='Payments', organization_id=7)
