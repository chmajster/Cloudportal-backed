import ipaddress
import re
import secrets
from urllib.parse import quote
import httpx
from fastapi import HTTPException
from app.providers.base import InfrastructureProvider
from app.security.core import decrypt_secret


def create_api_token(endpoint, username, password, verify_ssl=True):
    """Authenticate once with a password, create a PVE API token, then verify that token."""
    base = endpoint.rstrip('/') + '/api2/json'
    token_name = 'cloudportal-' + secrets.token_hex(4)
    token_id = username + '!' + token_name
    try:
        with httpx.Client(verify=verify_ssl, timeout=httpx.Timeout(20, connect=5),
                          follow_redirects=False, trust_env=False) as client:
            auth = client.post(base + '/access/ticket', data={'username': username, 'password': password})
            if auth.status_code in {401, 403}:
                raise HTTPException(422, 'Proxmox login failed; verify username, realm and password')
            auth.raise_for_status()
            auth_data = auth.json()['data']
            ticket = auth_data.get('ticket')
            csrf = auth_data.get('CSRFPreventionToken')
            if not ticket or not csrf or str(ticket).startswith('PVE:tfa!'):
                raise HTTPException(422, 'Proxmox account requires additional authentication; create an API token manually or use an account without interactive 2FA for bootstrap')
            client.cookies.set('PVEAuthCookie', ticket)
            created = client.post(
                base + '/access/users/' + quote(username, safe='') + '/token/' + quote(token_name, safe=''),
                headers={'CSRFPreventionToken': csrf},
                data={'privsep': 0, 'comment': 'Created automatically by Cloudportal-backed'},
            )
            if created.status_code in {401, 403}:
                raise HTTPException(422, 'Proxmox user is not allowed to create this API token')
            created.raise_for_status()
            token_secret = created.json()['data']['value']
            if not token_secret:
                raise ValueError('Missing API token value')

            # Do not let the login cookie make the verification pass accidentally.
            client.cookies.clear()
            check = client.get(base + '/version', headers={
                'Authorization': 'PVEAPIToken=' + token_id + '=' + token_secret,
            })
            check.raise_for_status()
            version = check.json().get('data', {}).get('version')
            return {'token_id': token_id, 'token_secret': token_secret, 'version': version}
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(502, 'Proxmox connection or API token creation failed; verify endpoint, TLS and permissions') from None
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, 'Proxmox returned an invalid response while creating the API token') from None


class ProxmoxProvider(InfrastructureProvider):
    def __init__(self, credential):
        self.endpoint = credential.endpoint.rstrip('/') + '/api2/json'
        self.username = credential.username
        self.secret = decrypt_secret(credential)
        self.verify_ssl = credential.verify_ssl

    def _get(self, path):
        try:
            with httpx.Client(verify=self.verify_ssl, timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False) as client:
                headers = {}
                if self.secret.get('token_id') and self.secret.get('token_secret'):
                    token_id = self.secret['token_id']
                    if '!' not in token_id:
                        token_id = self.username + '!' + token_id
                    headers['Authorization'] = 'PVEAPIToken=' + token_id + '=' + self.secret['token_secret']
                else:
                    auth = client.post(self.endpoint + '/access/ticket', data={'username': self.username, 'password': self.secret.get('password', '')})
                    auth.raise_for_status()
                    ticket = auth.json()['data']
                    client.cookies.set('PVEAuthCookie', ticket['ticket'])
                    headers['CSRFPreventionToken'] = ticket['CSRFPreventionToken']
                response = client.get(self.endpoint + path, headers=headers)
                response.raise_for_status()
                return response.json()['data']
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(502, 'Proxmox connection or authentication failed; verify endpoint, TLS and credential permissions') from None

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
            return [v for v in rows if v.get('type') == 'qemu' and bool(v.get('template')) == (resource == 'templates')
                    and (node is None or v.get('node') == node)]
        if resource == 'networks':
            nodes = [{'node': node}] if node else self._get('/nodes')
            return [dict(network, node=n['node']) for n in nodes
                    for network in self._get('/nodes/' + quote(n['node'], safe='') + '/network')]
        raise HTTPException(422, 'Unknown resource')

    def guest_addresses(self, node, vm_id):
        data = self._get(f'/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/agent/network-get-interfaces')
        addresses = []
        for interface in data.get('result', []):
            for record in interface.get('ip-addresses', []):
                try:
                    address = ipaddress.ip_address(record['ip-address'])
                    if not address.is_loopback and not address.is_link_local and not address.is_unspecified and not address.is_multicast:
                        addresses.append(str(address))
                except (KeyError, ValueError):
                    continue
        return addresses
