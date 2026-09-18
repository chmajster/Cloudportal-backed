import ipaddress
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.providers.base import InfrastructureProvider
from app.security.core import decrypt_secret


def create_api_token(endpoint, username, password, token_name, *, verify_ssl=True, privilege_separation=True):
    """Create a Proxmox API token using a one-shot username/password login.

    The password is used only for the ticket request and is never returned or persisted.
    """
    base = endpoint.rstrip('/') + '/api2/json'
    try:
        with httpx.Client(
            verify=verify_ssl,
            timeout=httpx.Timeout(30, connect=5),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            auth = client.post(
                base + '/access/ticket',
                data={'username': username, 'password': password},
            )
            auth.raise_for_status()
            ticket = auth.json()['data']
            client.cookies.set('PVEAuthCookie', ticket['ticket'])
            response = client.post(
                base + '/access/users/' + quote(username, safe='') + '/token/' + quote(token_name, safe=''),
                headers={'CSRFPreventionToken': ticket['CSRFPreventionToken']},
                data={
                    'privsep': int(privilege_separation),
                    'comment': 'Managed by Cloudportal-backed',
                },
            )
            response.raise_for_status()
            data = response.json()['data']
            token_secret = data.get('value')
            if not token_secret:
                raise ValueError('Proxmox did not return token value')
            return {
                'token_id': data.get('full-tokenid') or f'{username}!{token_name}',
                'token_secret': token_secret,
            }
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(
            502,
            'Proxmox API token creation failed; verify endpoint, TLS, password and user-management permissions',
        ) from None


class ProxmoxProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.endpoint = credential.endpoint.rstrip('/') + '/api2/json'
        self.username = credential.username
        self.secret = decrypt_secret(credential)
        self.verify_ssl = credential.verify_ssl

    def _request(self, method, path, *, data=None):
        try:
            with httpx.Client(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(30, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                headers = {}
                if self.secret.get('token_id') and self.secret.get('token_secret'):
                    token_id = self.secret['token_id']
                    if '!' not in token_id:
                        token_id = self.username + '!' + token_id
                    headers['Authorization'] = 'PVEAPIToken=' + token_id + '=' + self.secret['token_secret']
                else:
                    auth = client.post(
                        self.endpoint + '/access/ticket',
                        data={'username': self.username, 'password': self.secret.get('password', '')},
                    )
                    auth.raise_for_status()
                    ticket = auth.json()['data']
                    client.cookies.set('PVEAuthCookie', ticket['ticket'])
                    headers['CSRFPreventionToken'] = ticket['CSRFPreventionToken']
                response = client.request(method, self.endpoint + path, headers=headers, data=data)
                response.raise_for_status()
                body = response.json()
                return body.get('data')
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(
                502,
                'Proxmox connection or authentication failed; verify endpoint, TLS and credential permissions',
            ) from None

    def _get(self, path):
        return self._request('GET', path)

    def _post(self, path, data=None):
        return self._request('POST', path, data=data)

    def _put(self, path, data=None):
        return self._request('PUT', path, data=data)

    def _delete(self, path, data=None):
        return self._request('DELETE', path, data=data)

    def test(self):
        data = self._get('/version')
        return {'ok': True, 'provider': 'proxmox', 'version': data.get('version')}

    def discover(self, resource, node=None):
        if resource == 'nodes':
            return self._get('/nodes')
        if resource == 'storages':
            return self._get('/storage') if not node else self._get('/nodes/' + quote(node, safe='') + '/storage')
        if resource == 'pools':
            return self._get('/pools')
        if resource in {'templates', 'vms'}:
            rows = self._get('/cluster/resources?type=vm')
            return [
                v
                for v in rows
                if v.get('type') == 'qemu'
                and bool(v.get('template')) == (resource == 'templates')
                and (node is None or v.get('node') == node)
            ]
        if resource == 'networks':
            nodes = [{'node': node}] if node else self._get('/nodes')
            return [
                dict(network, node=n['node'])
                for n in nodes
                for network in self._get('/nodes/' + quote(n['node'], safe='') + '/network')
            ]
        raise HTTPException(422, 'Unknown resource')

    def guest_addresses(self, node, vm_id):
        data = self._get(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/agent/network-get-interfaces'
        )
        addresses = []
        for interface in data.get('result', []):
            for record in interface.get('ip-addresses', []):
                try:
                    address = ipaddress.ip_address(record['ip-address'])
                    if (
                        not address.is_loopback
                        and not address.is_link_local
                        and not address.is_unspecified
                        and not address.is_multicast
                    ):
                        addresses.append(str(address))
                except (KeyError, ValueError):
                    continue
        return addresses

    def vm_status(self, node, vm_id):
        return self._get(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/status/current')

    def vm_config(self, node, vm_id):
        return self._get(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/config')

    def vm_power(self, node, vm_id, action):
        if action not in {'start', 'stop', 'shutdown', 'reboot', 'reset', 'suspend', 'resume'}:
            raise HTTPException(422, 'Unsupported VM power action')
        return self._post(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/status/{action}')

    def snapshots(self, node, vm_id):
        return self._get(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/snapshot')

    def create_snapshot(self, node, vm_id, snapname, description='', include_ram=False):
        return self._post(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/snapshot',
            {'snapname': snapname, 'description': description, 'vmstate': int(include_ram)},
        )

    def delete_snapshot(self, node, vm_id, snapname):
        return self._delete(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/snapshot/{quote(snapname, safe="")}'
        )

    def rollback_snapshot(self, node, vm_id, snapname):
        return self._post(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/snapshot/{quote(snapname, safe="")}/rollback'
        )

    def convert_to_template(self, node, vm_id):
        return self._post(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/template')

    def clone_vm(self, node, vm_id, *, new_vm_id, name, target=None, full=True, storage=None, pool=None):
        data = {'newid': int(new_vm_id), 'name': name, 'full': int(full)}
        if target:
            data['target'] = target
        if storage:
            data['storage'] = storage
        if pool:
            data['pool'] = pool
        return self._post(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/clone', data)

    def resize_disk(self, node, vm_id, *, disk, grow_gib):
        return self._put(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/resize',
            {'disk': disk, 'size': f'+{int(grow_gib)}G'},
        )

    def update_vm_config(self, node, vm_id, **values):
        allowed = {key: value for key, value in values.items() if value is not None}
        if not allowed:
            raise HTTPException(422, 'No VM configuration fields supplied')
        return self._put(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/config', allowed)

    def migrate_vm(self, node, vm_id, *, target, online=False, with_local_disks=False):
        return self._post(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/migrate',
            {
                'target': target,
                'online': int(online),
                'with-local-disks': int(with_local_disks),
            },
        )

    def delete_vm(self, node, vm_id, *, purge=False, destroy_unreferenced_disks=False):
        return self._delete(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}',
            {
                'purge': int(purge),
                'destroy-unreferenced-disks': int(destroy_unreferenced_disks),
            },
        )



    def backups(self, node, storage, vm_id=None):
        rows = self._get(
            f'/nodes/{quote(node, safe="")}/storage/{quote(storage, safe="")}/content?content=backup'
        )
        if vm_id is not None:
            rows = [row for row in rows if int(row.get('vmid', -1)) == int(vm_id)]
        return rows

    def backup_vm(self, node, vm_id, *, storage, mode='snapshot', compress='zstd', notes=None):
        data = {
            'vmid': int(vm_id),
            'storage': storage,
            'mode': mode,
            'compress': compress,
        }
        if notes:
            data['notes-template'] = notes
        return self._post(f'/nodes/{quote(node, safe="")}/vzdump', data)

    def restore_vm(self, node, *, vm_id, archive, storage=None, unique=True):
        data = {
            'vmid': int(vm_id),
            'archive': archive,
            'unique': int(unique),
        }
        if storage:
            data['storage'] = storage
        return self._post(f'/nodes/{quote(node, safe="")}/qemu', data)

    def console_session(self, node, vm_id):
        data = self._post(
            f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/vncproxy',
            {'websocket': 1},
        )
        if not isinstance(data, dict) or not data.get('ticket') or not data.get('port'):
            raise HTTPException(502, 'Proxmox did not return a console ticket')
        return {
            'ticket': data['ticket'],
            'port': int(data['port']),
            'user': data.get('user'),
            'cert': data.get('cert'),
            'websocket_path': f'/api2/json/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/vncwebsocket',
            'origin': self.endpoint.removesuffix('/api2/json'),
        }

    def task_status(self, node, upid):
        return self._get(
            f'/nodes/{quote(node, safe="")}/tasks/{quote(upid, safe="")}/status'
        )
