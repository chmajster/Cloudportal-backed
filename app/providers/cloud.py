from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.providers.base import InfrastructureProvider
from app.security.core import decrypt_secret


def _error(provider):
    return HTTPException(502, f'{provider} discovery failed; verify credentials, endpoint, scope and permissions')


class AWSProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.secret = decrypt_secret(credential)

    def _client(self, service, region='us-east-1'):
        import boto3
        from botocore.config import Config
        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=self.secret['access_key_id'],
            aws_secret_access_key=self.secret['secret_access_key'],
            aws_session_token=self.secret.get('session_token'),
            config=Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 1}),
        )

    def test(self):
        try:
            identity = self._client('sts').get_caller_identity()
            return {'ok': True, 'provider': 'aws', 'version': identity.get('Account')}
        except Exception:
            raise _error('AWS') from None

    def discover(self, resource, node=None):
        region = node or 'us-east-1'
        try:
            ec2 = self._client('ec2', region)
            if resource == 'vms':
                rows = []
                for reservation in ec2.describe_instances().get('Reservations', []):
                    for vm in reservation.get('Instances', []):
                        tags = {item.get('Key'): item.get('Value') for item in vm.get('Tags', [])}
                        rows.append({
                            'id': vm.get('InstanceId'), 'vmid': vm.get('InstanceId'),
                            'name': tags.get('Name') or vm.get('InstanceId'),
                            'node': vm.get('Placement', {}).get('AvailabilityZone'),
                            'status': vm.get('State', {}).get('Name'), 'type': vm.get('InstanceType'),
                            'tags': ';'.join(f'{k}={v}' for k, v in sorted(tags.items()) if k and v),
                        })
                return rows
            if resource == 'templates':
                return [{'id': row.get('ImageId'), 'vmid': row.get('ImageId'), 'name': row.get('Name') or row.get('ImageId'),
                         'status': row.get('State'), 'type': 'ami'} for row in ec2.describe_images(Owners=['self']).get('Images', [])]
            if resource == 'networks':
                return [{'node': row.get('AvailabilityZone'), 'iface': row.get('SubnetId'), 'type': 'subnet',
                         'cidr': row.get('CidrBlock'), 'comments': f"VPC {row.get('VpcId', '')}"}
                        for row in ec2.describe_subnets().get('Subnets', [])]
            if resource == 'storages':
                return [{'storage': row.get('VolumeId'), 'type': row.get('VolumeType'), 'content': 'block',
                         'nodes': ','.join(a.get('InstanceId', '') for a in row.get('Attachments', [])),
                         'active': row.get('State') == 'in-use', 'total': int(row.get('Size', 0)) * 1024**3}
                        for row in ec2.describe_volumes().get('Volumes', [])]
            if resource == 'nodes':
                return [{'node': row.get('ZoneName'), 'status': row.get('State')}
                        for row in ec2.describe_availability_zones().get('AvailabilityZones', [])]
            if resource == 'pools':
                return [{'poolid': row.get('VpcId'), 'comment': row.get('CidrBlock', '')}
                        for row in ec2.describe_vpcs().get('Vpcs', [])]
            raise HTTPException(422, 'Unknown AWS discovery resource')
        except HTTPException:
            raise
        except Exception:
            raise _error('AWS') from None

    def guest_addresses(self, node, vm_id):
        return []


class AzureProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.secret = decrypt_secret(credential)
        self.verify_ssl = credential.verify_ssl

    def _token(self, client):
        response = client.post(
            'https://login.microsoftonline.com/' + quote(self.secret['tenant_id'], safe='') + '/oauth2/v2.0/token',
            data={'client_id': self.secret['client_id'], 'client_secret': self.secret['client_secret'],
                  'grant_type': 'client_credentials', 'scope': 'https://management.azure.com/.default'},
        )
        response.raise_for_status()
        return response.json()['access_token']

    def _get(self, path):
        try:
            with httpx.Client(verify=self.verify_ssl, timeout=20, follow_redirects=False, trust_env=False) as client:
                token = self._token(client)
                response = client.get('https://management.azure.com' + path, headers={'Authorization': 'Bearer ' + token})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, KeyError, ValueError):
            raise _error('Azure') from None

    def test(self):
        subscription = self.secret['subscription_id']
        self._get(f'/subscriptions/{quote(subscription, safe="")}?api-version=2022-12-01')
        return {'ok': True, 'provider': 'azure'}

    def discover(self, resource, node=None):
        sub = quote(self.secret['subscription_id'], safe='')
        if resource == 'vms':
            values = self._get(f'/subscriptions/{sub}/providers/Microsoft.Compute/virtualMachines?api-version=2024-07-01').get('value', [])
            return [{'id': row.get('id'), 'vmid': row.get('id'), 'name': row.get('name'), 'node': row.get('location'),
                     'status': 'defined', 'type': row.get('properties', {}).get('hardwareProfile', {}).get('vmSize'),
                     'tags': ';'.join(f'{k}={v}' for k, v in sorted((row.get('tags') or {}).items()))} for row in values]
        if resource == 'templates':
            values = self._get(f'/subscriptions/{sub}/providers/Microsoft.Compute/images?api-version=2024-03-01').get('value', [])
            return [{'id': row.get('id'), 'vmid': row.get('id'), 'name': row.get('name'), 'node': row.get('location'), 'status': 'available', 'type': 'image'} for row in values]
        if resource == 'networks':
            values = self._get(f'/subscriptions/{sub}/providers/Microsoft.Network/virtualNetworks?api-version=2024-05-01').get('value', [])
            return [{'node': row.get('location'), 'iface': row.get('name'), 'type': 'vnet',
                     'cidr': ','.join(row.get('properties', {}).get('addressSpace', {}).get('addressPrefixes', [])),
                     'comments': row.get('id', '')} for row in values]
        if resource == 'storages':
            values = self._get(f'/subscriptions/{sub}/providers/Microsoft.Compute/disks?api-version=2024-03-02').get('value', [])
            return [{'storage': row.get('name'), 'type': row.get('sku', {}).get('name'), 'content': 'block',
                     'nodes': row.get('managedBy') or '', 'active': bool(row.get('managedBy')),
                     'total': int(row.get('properties', {}).get('diskSizeGB') or 0) * 1024**3} for row in values]
        if resource == 'nodes':
            values = self._get(f'/subscriptions/{sub}/locations?api-version=2022-12-01').get('value', [])
            return [{'node': row.get('name'), 'status': 'available'} for row in values]
        if resource == 'pools':
            values = self._get(f'/subscriptions/{sub}/resourcegroups?api-version=2021-04-01').get('value', [])
            return [{'poolid': row.get('name'), 'comment': row.get('location', '')} for row in values]
        raise HTTPException(422, 'Unknown Azure discovery resource')

    def guest_addresses(self, node, vm_id):
        return []


class VMwareProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.endpoint = credential.endpoint.rstrip('/')
        self.username = credential.username
        self.secret = decrypt_secret(credential)
        self.verify_ssl = credential.verify_ssl

    def _request(self, path):
        try:
            with httpx.Client(verify=self.verify_ssl, timeout=20, follow_redirects=False, trust_env=False) as client:
                auth = client.post(self.endpoint + '/api/session', auth=(self.username, self.secret['password']))
                auth.raise_for_status()
                token = auth.json()
                headers = {'vmware-api-session-id': token}
                response = client.get(self.endpoint + path, headers=headers)
                response.raise_for_status()
                result = response.json()
                client.delete(self.endpoint + '/api/session', headers=headers).raise_for_status()
                return result
        except (httpx.HTTPError, KeyError, ValueError):
            raise _error('VMware') from None

    def test(self):
        self._request('/api/vcenter/vm')
        return {'ok': True, 'provider': 'vmware'}

    def discover(self, resource, node=None):
        if resource in {'vms', 'templates'}:
            rows = self._request('/api/vcenter/vm')
            return [{'id': row.get('vm'), 'vmid': row.get('vm'), 'name': row.get('name'),
                     'status': row.get('power_state'), 'type': 'vmware-vm',
                     'cpu': row.get('cpu_count'), 'mem': row.get('memory_size_MiB')} for row in rows]
        if resource == 'networks':
            return [{'iface': row.get('network'), 'type': row.get('type'), 'comments': row.get('name') or ''}
                    for row in self._request('/api/vcenter/network')]
        if resource == 'storages':
            return [{'storage': row.get('datastore'), 'type': row.get('type'), 'content': 'datastore', 'active': True,
                     'total': row.get('capacity'), 'avail': row.get('free_space')} for row in self._request('/api/vcenter/datastore')]
        if resource == 'nodes':
            return [{'node': row.get('host'), 'status': row.get('connection_state')}
                    for row in self._request('/api/vcenter/host')]
        if resource == 'pools':
            return [{'poolid': row.get('resource_pool'), 'comment': row.get('name') or ''}
                    for row in self._request('/api/vcenter/resource-pool')]
        raise HTTPException(422, 'Unknown VMware discovery resource')

    def guest_addresses(self, node, vm_id):
        return []


class OpenStackProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.endpoint = credential.endpoint.rstrip('/')
        if not self.endpoint.endswith('/v3'):
            self.endpoint += '/v3'
        self.username = credential.username
        self.secret = decrypt_secret(credential)
        self.verify_ssl = credential.verify_ssl

    def _session(self):
        client = httpx.Client(verify=self.verify_ssl, timeout=20, follow_redirects=False, trust_env=False)
        response = client.post(self.endpoint + '/auth/tokens', json={'auth': {
            'identity': {'methods': ['password'], 'password': {'user': {'name': self.username,
                'password': self.secret['password'], 'domain': {'name': self.secret.get('domain_name', 'Default')}}}},
            'scope': {'project': {'name': self.secret['project_name'], 'domain': {'name': self.secret.get('domain_name', 'Default')}}},
        }})
        response.raise_for_status()
        token = response.headers['X-Subject-Token']
        catalog = response.json()['token']['catalog']
        return client, token, catalog

    @staticmethod
    def _endpoint(catalog, service_type):
        for service in catalog:
            if service.get('type') == service_type:
                for endpoint in service.get('endpoints', []):
                    if endpoint.get('interface') == 'public':
                        return endpoint.get('url', '').rstrip('/')
        raise KeyError(service_type)

    def _get(self, service, path):
        client = None
        try:
            client, token, catalog = self._session()
            base = self._endpoint(catalog, service)
            response = client.get(base + path, headers={'X-Auth-Token': token})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, KeyError, ValueError):
            raise _error('OpenStack') from None
        finally:
            if client:
                client.close()

    def test(self):
        client = None
        try:
            client, _, _ = self._session()
            return {'ok': True, 'provider': 'openstack'}
        except Exception:
            raise _error('OpenStack') from None
        finally:
            if client:
                client.close()

    def discover(self, resource, node=None):
        if resource == 'vms':
            rows = self._get('compute', '/servers/detail').get('servers', [])
            return [{'id': row.get('id'), 'vmid': row.get('id'), 'name': row.get('name'),
                     'node': row.get('OS-EXT-AZ:availability_zone'), 'status': row.get('status'),
                     'type': 'openstack-server'} for row in rows]
        if resource == 'templates':
            rows = self._get('image', '/v2/images').get('images', [])
            return [{'id': row.get('id'), 'vmid': row.get('id'), 'name': row.get('name'),
                     'status': row.get('status'), 'type': row.get('disk_format')} for row in rows]
        if resource == 'networks':
            rows = self._get('network', '/v2.0/networks').get('networks', [])
            return [{'iface': row.get('id'), 'type': 'network', 'active': row.get('admin_state_up'),
                     'comments': row.get('name') or ''} for row in rows]
        if resource == 'storages':
            rows = self._get('volumev3', '/volumes/detail').get('volumes', [])
            return [{'storage': row.get('id'), 'type': row.get('volume_type'), 'content': 'block',
                     'active': row.get('status') == 'in-use', 'total': int(row.get('size') or 0) * 1024**3,
                     'nodes': row.get('name') or ''} for row in rows]
        raise HTTPException(422, 'OpenStack discovery supports vms, templates, networks and storages')

    def guest_addresses(self, node, vm_id):
        return []
