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

    def ensure_inventory(self, *, inventory_id=None, name='CloudPortal'):
        self.seen['inventory'] = {'inventory_id': inventory_id, 'name': name}
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
    )

    assert result == {
        'inventory_id': 42,
        'inventory_name': 'CloudPortal',
        'host_id': 101,
        'host_name': 'srv001',
        'groups': ['env-prod', 'apmid-leo'],
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
