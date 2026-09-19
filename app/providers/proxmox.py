import ipaddress
import re
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import HTTPException

from app.providers.base import InfrastructureProvider
from app.security.core import decrypt_secret


def _certificate_verification_failed(error):
    current = error
    while current is not None:
        message = str(current).lower()
        if 'certificate_verify_failed' in message or 'certificate verify failed' in message:
            return True
        current = getattr(current, '__cause__', None)
    return False


def _canonical_proxmox_endpoint(endpoint, scheme=None):
    raw = endpoint.strip().rstrip('/')
    if not raw:
        raise HTTPException(422, 'Adres Proxmox jest wymagany.')
    explicit_scheme = '://' in raw
    if not explicit_scheme:
        try:
            address = ipaddress.ip_address(raw)
            if address.version == 6:
                raw = f'[{raw}]'
        except ValueError:
            pass
        if scheme is None:
            raise HTTPException(422, 'Nie udało się ustalić protokołu adresu Proxmox.')
        raw = f'{scheme}://{raw}'

    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise HTTPException(
            422,
            'Adres Proxmox musi używać HTTP lub HTTPS i zawierać wyłącznie host/IP oraz opcjonalny port.',
        )
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(422, 'Nieprawidłowy port endpointu Proxmox.') from None
    if port is None and not explicit_scheme:
        port = 8006
    host = parsed.hostname
    if ':' in host:
        host = f'[{host}]'
    authority = host + (f':{port}' if port is not None else '')
    return f'{parsed.scheme}://{authority}'


def resolve_proxmox_endpoint(endpoint, *, verify_ssl=True):
    raw = endpoint.strip().rstrip('/')
    if '://' in raw:
        return _canonical_proxmox_endpoint(raw)

    certificate_error = False
    failures = []
    for scheme in ('https', 'http'):
        candidate = _canonical_proxmox_endpoint(raw, scheme)
        try:
            with httpx.Client(
                verify=verify_ssl if scheme == 'https' else True,
                timeout=httpx.Timeout(5, connect=3),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                client.get(candidate + '/api2/json/version')
            return candidate
        except httpx.TransportError as error:
            certificate_error = certificate_error or _certificate_verification_failed(error)
            failures.append(f'{scheme}: {error.__class__.__name__}')

    if certificate_error:
        raise HTTPException(
            502,
            'Weryfikacja certyfikatu TLS nie powiodła się podczas wykrywania endpointu Proxmox. '
            'Włącz akceptację certyfikatu self-signed / niezaufanego albo zainstaluj zaufany certyfikat i spróbuj ponownie.',
        )
    raise HTTPException(
        502,
        'Nie udało się wykryć protokołu Proxmox. Sprawdź adres IP/hostname i port (domyślnie 8006) '
        'albo podaj jawnie http:// lub https://.',
    )


def _proxmox_http_exception(error, operation):
    if _certificate_verification_failed(error):
        return HTTPException(
            502,
            f'{operation}: weryfikacja certyfikatu TLS nie powiodła się. '
            'Włącz akceptację certyfikatu self-signed / niezaufanego albo popraw certyfikat serwera.',
        )
    if isinstance(error, httpx.ConnectTimeout):
        return HTTPException(
            502,
            f'{operation}: przekroczono czas nawiązania połączenia z Proxmox. '
            'Sprawdź adres, port, routing i reguły firewalla.',
        )
    if isinstance(error, httpx.ReadTimeout):
        return HTTPException(
            502,
            f'{operation}: Proxmox nie odpowiedział w wymaganym czasie. '
            'Sprawdź stan API i obciążenie serwera.',
        )
    if isinstance(error, httpx.ConnectError):
        return HTTPException(
            502,
            f'{operation}: nie można połączyć się z API Proxmox. '
            'Sprawdź adres, port, protokół HTTP/HTTPS, routing i firewall.',
        )
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        reasons = {
            400: 'Proxmox odrzucił parametry żądania.',
            401: 'Logowanie zostało odrzucone. Sprawdź użytkownika, realm i hasło lub token.',
            403: 'Konto nie ma wymaganych uprawnień do tej operacji.',
            404: 'Nie znaleziono zasobu API. Sprawdź endpoint, użytkownika i nazwę tokenu.',
            409: 'Zasób już istnieje albo wystąpił konflikt po stronie Proxmox.',
        }
        reason = reasons.get(status, 'Proxmox zwrócił błąd HTTP.')
        return HTTPException(
            502,
            f'{operation}: {reason} Kod odpowiedzi Proxmox: HTTP {status}.',
        )
    return HTTPException(
        502,
        f'{operation}: połączenie z API Proxmox zakończyło się błędem. '
        'Sprawdź endpoint, protokół, dane uwierzytelniające i uprawnienia.',
    )


def _proxmox_token_exists(client, base, username, token_name, headers):
    """Verify a suspected duplicate by reading the user's actual token list."""
    try:
        response = client.get(
            base + '/access/users/' + quote(username, safe='') + '/token',
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json().get('data') or []
    except httpx.HTTPError as error:
        raise HTTPException(
            502,
            {
                'code': 'proxmox_token_duplicate_check_failed',
                'message': (
                    'Proxmox zwrócił HTTP 400 podczas tworzenia tokenu, ale Cloudportal nie mógł '
                    'sprawdzić listy istniejących tokenów. Nie można bezpiecznie uznać tego błędu za duplikat.'
                ),
                'proxmox_status': 400,
                'check_error': error.__class__.__name__,
            },
        ) from None
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            502,
            {
                'code': 'proxmox_token_duplicate_check_failed',
                'message': (
                    'Proxmox zwrócił HTTP 400 podczas tworzenia tokenu, a odpowiedź z listą tokenów '
                    'miała nieprawidłowy format. Nie można potwierdzić duplikatu.'
                ),
                'proxmox_status': 400,
            },
        ) from None

    expected_full_id = f'{username}!{token_name}'
    return any(
        isinstance(row, dict) and (
            row.get('tokenid') == token_name
            or row.get('full-tokenid') == expected_full_id
            or row.get('full_tokenid') == expected_full_id
        )
        for row in rows
    )


def _suggest_proxmox_token_name(token_name):
    match = re.fullmatch(r'(.+?)-(\d+)', token_name)
    if match:
        base = match.group(1)
        number = int(match.group(2)) + 1
    else:
        base = token_name
        number = 2
    suffix = f'-{number}'
    max_base = max(1, 63 - len(suffix))
    base = base[:max_base].rstrip('._-') or 'cloudportal'
    return base + suffix


def _proxmox_duplicate_token_exception(username, token_name):
    suggested = _suggest_proxmox_token_name(token_name)
    return HTTPException(
        409,
        {
            'code': 'proxmox_token_duplicate',
            'message': f'Token API Proxmox „{username}!{token_name}” już istnieje.',
            'token_name': token_name,
            'suggested_token_name': suggested,
        },
    )


def test_proxmox_connection(endpoint, username, secret, *, verify_ssl=True):
    """Test a draft Proxmox credential without persisting the supplied secret."""
    base = endpoint.rstrip('/') + '/api2/json'
    started = monotonic()
    auth_mode = 'token' if secret.get('token_id') and secret.get('token_secret') else 'password'
    try:
        with httpx.Client(
            verify=verify_ssl,
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            headers = {}
            if auth_mode == 'token':
                token_id = secret['token_id']
                if '!' not in token_id:
                    token_id = username + '!' + token_id
                headers['Authorization'] = 'PVEAPIToken=' + token_id + '=' + secret['token_secret']
            else:
                password = secret.get('password')
                if not password:
                    raise HTTPException(422, 'Podaj hasło Proxmox albo komplet danych tokenu API.')
                try:
                    auth = client.post(
                        base + '/access/ticket',
                        data={'username': username, 'password': password},
                    )
                    auth.raise_for_status()
                except httpx.HTTPError as error:
                    raise _proxmox_http_exception(error, 'Test logowania do Proxmox VE') from None
                ticket = auth.json()['data']
                client.cookies.set('PVEAuthCookie', ticket['ticket'])
                headers['CSRFPreventionToken'] = ticket['CSRFPreventionToken']

            try:
                response = client.get(base + '/version', headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise _proxmox_http_exception(error, 'Test dostępu do API Proxmox VE') from None
            data = response.json()['data']
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            502,
            'Proxmox zwrócił nieprawidłową odpowiedź podczas testu połączenia. '
            'Sprawdź wersję Proxmox VE, endpoint i konfigurację reverse proxy.',
        ) from None

    return {
        'ok': True,
        'provider': 'proxmox',
        'version': str(data.get('version')) if data.get('version') is not None else None,
        'endpoint': endpoint,
        'username': username,
        'auth_mode': auth_mode,
        'verify_ssl': verify_ssl,
        'latency_ms': max(0, round((monotonic() - started) * 1000)),
        'message': 'Połączenie z Proxmox VE działa poprawnie.',
    }


def create_api_token(endpoint, username, password, token_name, *, verify_ssl=True, privilege_separation=True):
    """Create a Proxmox API token using a one-shot username/password login.

    The password is used only for the ticket request and is never returned or persisted.
    """
    base = endpoint.rstrip('/') + '/api2/json'
    phase = 'Logowanie do Proxmox VE'
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
            phase = 'Tworzenie tokenu API Proxmox'
            response = client.post(
                base + '/access/users/' + quote(username, safe='') + '/token/' + quote(token_name, safe=''),
                headers={'CSRFPreventionToken': ticket['CSRFPreventionToken']},
                data={
                    'privsep': int(privilege_separation),
                    'comment': 'Managed by Cloudportal-backed',
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 400:
                    duplicate = _proxmox_token_exists(
                        client,
                        base,
                        username,
                        token_name,
                        {'CSRFPreventionToken': ticket['CSRFPreventionToken']},
                    )
                    if duplicate:
                        raise _proxmox_duplicate_token_exception(username, token_name) from None
                    raise HTTPException(
                        502,
                        {
                            'code': 'proxmox_token_create_rejected',
                            'message': (
                                'Proxmox odrzucił utworzenie tokenu (HTTP 400). '
                                'Cloudportal sprawdził listę tokenów i potwierdził, że wskazana nazwa '
                                'nie jest duplikatem. Sprawdź parametry tokenu i uprawnienia użytkownika.'
                            ),
                            'proxmox_status': 400,
                            'duplicate_checked': True,
                            'duplicate': False,
                        },
                    ) from None
                raise
            data = response.json()['data']
            token_secret = data.get('value')
            if not token_secret:
                raise ValueError('missing-token-value')
            return {
                'token_id': data.get('full-tokenid') or f'{username}!{token_name}',
                'token_secret': token_secret,
            }
    except HTTPException:
        raise
    except httpx.HTTPError as error:
        raise _proxmox_http_exception(error, phase) from None
    except (KeyError, ValueError):
        if phase == 'Tworzenie tokenu API Proxmox':
            raise HTTPException(
                502,
                'Tworzenie tokenu API Proxmox nie powiodło się: serwer nie zwrócił kompletnego tokenu. '
                'Sprawdź nazwę tokenu, uprawnienia do zarządzania tokenami użytkownika i logi Proxmox.',
            ) from None
        raise HTTPException(
            502,
            'Logowanie do Proxmox VE nie powiodło się: odpowiedź API była niekompletna. '
            'Sprawdź użytkownika, realm, hasło i endpoint.',
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
        except httpx.HTTPError as error:
            if _certificate_verification_failed(error):
                raise HTTPException(
                    502,
                    'Weryfikacja certyfikatu TLS Proxmox nie powiodła się. '
                    'Włącz akceptację certyfikatu self-signed / niezaufanego albo popraw certyfikat serwera.',
                ) from None
            raise HTTPException(
                502,
                'Połączenie lub uwierzytelnienie Proxmox nie powiodło się. Sprawdź endpoint, protokół, dane dostępowe i uprawnienia.',
            ) from None
        except (KeyError, ValueError):
            raise HTTPException(
                502,
                'Połączenie lub uwierzytelnienie Proxmox nie powiodło się. Sprawdź endpoint, protokół, dane dostępowe i uprawnienia.',
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
            {'websocket': 1, 'generate-password': 1},
        )
        if (
            not isinstance(data, dict)
            or not data.get('ticket')
            or not data.get('port')
            or not data.get('password')
        ):
            raise HTTPException(502, 'Proxmox did not return complete noVNC proxy credentials')
        return {
            'ticket': data['ticket'],
            'port': int(data['port']),
            'password': data['password'],
        }

    def console_auth_headers(self):
        if self.secret.get('token_id') and self.secret.get('token_secret'):
            token_id = self.secret['token_id']
            if '!' not in token_id:
                token_id = self.username + '!' + token_id
            return {'Authorization': 'PVEAPIToken=' + token_id + '=' + self.secret['token_secret']}
        try:
            with httpx.Client(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(30, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    self.endpoint + '/access/ticket',
                    data={'username': self.username, 'password': self.secret.get('password', '')},
                )
                response.raise_for_status()
                ticket = response.json()['data']['ticket']
                return {'Cookie': 'PVEAuthCookie=' + ticket}
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(502, 'Unable to authenticate Proxmox console proxy') from None

    def console_websocket_url(self, node, vm_id, port, ticket):
        origin = self.endpoint.removesuffix('/api2/json')
        if origin.startswith('https://'):
            websocket_origin = 'wss://' + origin[len('https://'):]
        elif origin.startswith('http://'):
            websocket_origin = 'ws://' + origin[len('http://'):]
        else:
            raise HTTPException(502, 'Unsupported Proxmox endpoint protocol')
        path = f'/api2/json/nodes/{quote(node, safe="")}/qemu/{int(vm_id)}/vncwebsocket'
        query = urlencode({'port': int(port), 'vncticket': ticket})
        return websocket_origin + path + '?' + query

    def novnc_asset(self, asset):
        asset = asset.lstrip('/')
        if (
            not asset
            or '..' in asset
            or not re.fullmatch(r'[A-Za-z0-9._/-]+', asset)
            or asset.rsplit('.', 1)[-1].lower() not in {
                'js', 'css', 'html', 'svg', 'png', 'gif', 'jpg', 'jpeg', 'woff', 'woff2', 'ttf', 'map',
            }
        ):
            raise HTTPException(404, 'noVNC asset not found')
        try:
            with httpx.Client(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(30, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(
                    self.endpoint.removesuffix('/api2/json') + '/novnc/' + asset,
                    headers=self.console_auth_headers(),
                )
                response.raise_for_status()
                return response.content, response.headers.get('content-type', 'application/octet-stream')
        except httpx.HTTPError:
            raise HTTPException(502, 'Unable to proxy Proxmox noVNC asset') from None

    def task_status(self, node, upid):
        return self._get(
            f'/nodes/{quote(node, safe="")}/tasks/{quote(upid, safe="")}/status'
        )
