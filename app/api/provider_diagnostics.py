import re
import shutil
import socket
import ssl
import subprocess
from time import monotonic
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.common import find
from app.database import get_db
from app.models import Credential, Provider
from app.providers.registry import provider_for
from app.providers.settings import require_platform_enabled
from app.resource_scope.http import require
from app.security.core import audit


router = APIRouter(prefix='/diagnostics', tags=['diagnostics'])

_PROVIDER_LABELS = {
    'proxmox': 'Proxmox VE',
    'vmware': 'VMware vCenter',
    'aws': 'Amazon Web Services',
    'azure': 'Microsoft Azure',
    'openstack': 'OpenStack',
}

_DISCOVERY_RESOURCES = {
    'proxmox': ('nodes', 'storages', 'networks', 'templates', 'pools', 'vms'),
    'vmware': ('nodes', 'storages', 'networks', 'templates', 'pools', 'vms'),
    'aws': ('nodes', 'networks', 'storages', 'templates', 'pools', 'vms'),
    'azure': ('nodes', 'networks', 'storages', 'templates', 'pools', 'vms'),
    'openstack': ('vms', 'templates', 'networks', 'storages'),
}

_QUICK_DISCOVERY = {
    'proxmox': ('nodes',),
    'vmware': ('nodes',),
    'aws': (),
    'azure': ('nodes',),
    'openstack': ('vms',),
}


def _check(check_id, label, layer, status, message, *, latency_ms=None, critical=False, details=None):
    return {
        'id': check_id,
        'label': label,
        'layer': layer,
        'status': status,
        'message': message,
        'latency_ms': latency_ms,
        'critical': critical,
        'details': details or {},
    }


def _target_from_url(label, raw_url, verify_ssl=True):
    raw = str(raw_url or '').strip()
    if not raw:
        return None
    parsed = urlsplit(raw if '://' in raw else 'https://' + raw)
    if not parsed.hostname:
        return None
    scheme = parsed.scheme.lower() or 'https'
    try:
        port = parsed.port or (443 if scheme == 'https' else 80)
    except ValueError:
        return None
    return {
        'label': label,
        'url': raw if '://' in raw else f'{scheme}://{raw}',
        'host': parsed.hostname,
        'port': port,
        'scheme': scheme,
        'verify_ssl': bool(verify_ssl),
    }


def _network_targets(credential):
    kind = str(credential.type or '').lower()
    if kind in {'proxmox', 'vmware', 'openstack'}:
        target = _target_from_url(_PROVIDER_LABELS.get(kind, kind), credential.endpoint, credential.verify_ssl)
        return [target] if target else []
    if kind == 'aws':
        return [
            _target_from_url('AWS STS', 'https://sts.amazonaws.com', True),
        ]
    if kind == 'azure':
        return [
            _target_from_url('Azure Identity', 'https://login.microsoftonline.com', True),
            _target_from_url('Azure Resource Manager', 'https://management.azure.com', True),
        ]
    target = _target_from_url(_PROVIDER_LABELS.get(kind, kind), credential.endpoint, credential.verify_ssl)
    return [target] if target else []


def _dns_check(target):
    started = monotonic()
    try:
        info = socket.getaddrinfo(target['host'], target['port'], type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return _check(
            'dns',
            f"DNS: {target['label']}",
            'dns',
            'fail',
            f"Nie można rozwiązać nazwy {target['host']}. Sprawdź DNS i konfigurację resolvera.",
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'error': exc.__class__.__name__},
        )
    addresses = []
    for row in info:
        address = row[4][0]
        if address not in addresses:
            addresses.append(address)
    return _check(
        'dns',
        f"DNS: {target['label']}",
        'dns',
        'pass',
        f"DNS działa. Rozwiązano {target['host']} na {', '.join(addresses[:4])}.",
        latency_ms=round((monotonic() - started) * 1000),
        critical=True,
        details={'addresses': addresses[:8]},
    )


def _ping_check(target):
    binary = shutil.which('ping')
    if not binary:
        return _check(
            'icmp',
            f"Ping: {target['label']}",
            'icmp',
            'skipped',
            'Polecenie ping nie jest dostępne w kontenerze backendu. Test ICMP pominięto.',
        )
    args = [binary, '-c', '1', '-W', '2', target['host']]
    started = monotonic()
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check(
            'icmp',
            f"Ping: {target['label']}",
            'icmp',
            'warn',
            'Nie udało się wykonać testu ICMP. Nie blokuje to działania API przez TCP.',
            latency_ms=round((monotonic() - started) * 1000),
            details={'error': exc.__class__.__name__},
        )
    elapsed = round((monotonic() - started) * 1000)
    if result.returncode == 0:
        match = re.search(r'time[=<]([0-9.]+)\s*ms', result.stdout or '', re.IGNORECASE)
        latency = float(match.group(1)) if match else elapsed
        return _check(
            'icmp',
            f"Ping: {target['label']}",
            'icmp',
            'pass',
            f"Host {target['host']} odpowiada na ICMP.",
            latency_ms=latency,
        )
    return _check(
        'icmp',
        f"Ping: {target['label']}",
        'icmp',
        'warn',
        'Brak odpowiedzi ICMP. Ping może być blokowany przez firewall mimo poprawnego działania API.',
        latency_ms=elapsed,
    )


def _tcp_check(target):
    started = monotonic()
    try:
        with socket.create_connection((target['host'], target['port']), timeout=4):
            pass
    except socket.timeout:
        return _check(
            'tcp',
            f"TCP {target['port']}: {target['label']}",
            'tcp',
            'fail',
            f"Timeout TCP do {target['host']}:{target['port']}. Możliwa blokada firewalla, ACL albo problem routingu.",
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': 'timeout'},
        )
    except ConnectionRefusedError:
        return _check(
            'tcp',
            f"TCP {target['port']}: {target['label']}",
            'tcp',
            'fail',
            f"Host jest osiągalny, ale port {target['port']} odrzuca połączenie. Usługa może nie nasłuchiwać lub firewall aktywnie odrzuca ruch.",
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': 'connection_refused'},
        )
    except OSError as exc:
        return _check(
            'tcp',
            f"TCP {target['port']}: {target['label']}",
            'tcp',
            'fail',
            f"Nie można zestawić TCP do {target['host']}:{target['port']}. Sprawdź routing, NAT i firewall.",
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': exc.__class__.__name__},
        )
    return _check(
        'tcp',
        f"TCP {target['port']}: {target['label']}",
        'tcp',
        'pass',
        f"Połączenie TCP do {target['host']}:{target['port']} przechodzi.",
        latency_ms=round((monotonic() - started) * 1000),
        critical=True,
    )


def _tls_check(target):
    if target['scheme'] != 'https':
        return _check(
            'tls',
            f"TLS: {target['label']}",
            'tls',
            'skipped',
            'Endpoint korzysta z HTTP, dlatego test TLS pominięto.',
        )
    started = monotonic()
    context = ssl.create_default_context() if target['verify_ssl'] else ssl._create_unverified_context()
    try:
        with socket.create_connection((target['host'], target['port']), timeout=4) as raw:
            with context.wrap_socket(raw, server_hostname=target['host']) as secured:
                cipher = secured.cipher()
                certificate = secured.getpeercert() if target['verify_ssl'] else {}
                protocol = secured.version()
    except ssl.SSLCertVerificationError:
        return _check(
            'tls',
            f"TLS: {target['label']}",
            'tls',
            'fail',
            'Połączenie TLS dochodzi do serwera, ale walidacja certyfikatu nie powiodła się.',
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': 'certificate_verification_failed'},
        )
    except (ssl.SSLError, OSError, socket.timeout) as exc:
        return _check(
            'tls',
            f"TLS: {target['label']}",
            'tls',
            'fail',
            'Nie udało się zestawić sesji TLS. Sprawdź protokół, reverse proxy, certyfikat i firewall.',
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': exc.__class__.__name__},
        )
    status = 'pass' if target['verify_ssl'] else 'warn'
    message = (
        f"TLS działa ({protocol}). Certyfikat został zweryfikowany."
        if target['verify_ssl']
        else f"TLS działa ({protocol}), ale weryfikacja certyfikatu jest wyłączona dla tych danych dostępowych."
    )
    details = {'protocol': protocol, 'cipher': cipher[0] if cipher else None}
    if certificate:
        details['not_after'] = certificate.get('notAfter')
    return _check(
        'tls',
        f"TLS: {target['label']}",
        'tls',
        status,
        message,
        latency_ms=round((monotonic() - started) * 1000),
        details=details,
    )


def _http_probe_url(credential, target):
    kind = str(credential.type or '').lower()
    base = target['url'].rstrip('/')
    if kind == 'proxmox':
        return base + '/api2/json/version'
    if kind == 'vmware':
        return base + '/api'
    if kind == 'openstack':
        return base if base.endswith('/v3') else base + '/v3'
    return base + '/'


def _http_check(credential, target):
    url = _http_probe_url(credential, target)
    started = monotonic()
    try:
        with httpx.Client(
            verify=target['verify_ssl'],
            timeout=httpx.Timeout(8, connect=4),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return _check(
            'http',
            f"HTTP: {target['label']}",
            'http',
            'fail',
            'Warstwa HTTP nie odpowiada prawidłowo mimo próby połączenia. Sprawdź usługę API i reverse proxy.',
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': exc.__class__.__name__, 'url': url},
        )
    status_code = int(response.status_code)
    status = 'pass' if status_code < 500 else 'fail'
    message = (
        f"Endpoint HTTP odpowiada kodem {status_code}. Warstwa aplikacyjna jest osiągalna."
        if status == 'pass'
        else f"Endpoint HTTP odpowiada kodem {status_code}. Usługa po drugiej stronie zgłasza błąd serwera."
    )
    return _check(
        'http',
        f"HTTP: {target['label']}",
        'http',
        status,
        message,
        latency_ms=round((monotonic() - started) * 1000),
        critical=True,
        details={'status_code': status_code, 'url': url},
    )


def _detail_text(detail):
    if isinstance(detail, dict):
        return str(detail.get('message') or detail.get('detail') or detail.get('code') or 'Błąd providera.')
    if isinstance(detail, list):
        return '; '.join(str(item) for item in detail[:3])
    return str(detail or 'Błąd providera.')


def _auth_check(adapter, provider_type):
    started = monotonic()
    try:
        result = adapter.test() or {}
    except HTTPException as exc:
        return _check(
            'provider_auth',
            'Autoryzacja i API',
            'provider',
            'fail',
            _detail_text(exc.detail),
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'http_status': exc.status_code},
        )
    except Exception as exc:
        return _check(
            'provider_auth',
            'Autoryzacja i API',
            'provider',
            'fail',
            'Test providera zakończył się błędem. Sprawdź dane dostępowe, uprawnienia i endpoint.',
            latency_ms=round((monotonic() - started) * 1000),
            critical=True,
            details={'reason': exc.__class__.__name__},
        )
    details = {
        key: value for key, value in result.items()
        if key in {'provider', 'version', 'endpoint', 'username', 'auth_mode', 'verify_ssl'} and value is not None
    }
    return _check(
        'provider_auth',
        'Autoryzacja i API',
        'provider',
        'pass',
        f"Autoryzacja do {_PROVIDER_LABELS.get(provider_type, provider_type)} działa poprawnie.",
        latency_ms=round((monotonic() - started) * 1000),
        critical=True,
        details=details,
    )


def _sample_resource(row):
    if not isinstance(row, dict):
        return str(row)[:100]
    for key in ('name', 'node', 'id', 'vmid', 'storage', 'iface', 'poolid'):
        value = row.get(key)
        if value not in (None, ''):
            return str(value)[:100]
    return 'zasób'


def _discovery_check(adapter, resource):
    started = monotonic()
    try:
        rows = adapter.discover(resource)
    except HTTPException as exc:
        return _check(
            'discover_' + resource,
            'Pobranie: ' + resource,
            'discovery',
            'fail',
            f"API działa, ale nie udało się pobrać zasobu „{resource}”: {_detail_text(exc.detail)}",
            latency_ms=round((monotonic() - started) * 1000),
            details={'http_status': exc.status_code, 'resource': resource},
        )
    except Exception as exc:
        return _check(
            'discover_' + resource,
            'Pobranie: ' + resource,
            'discovery',
            'fail',
            f"API działa, ale odczyt zasobu „{resource}” zakończył się błędem.",
            latency_ms=round((monotonic() - started) * 1000),
            details={'reason': exc.__class__.__name__, 'resource': resource},
        )
    rows = rows if isinstance(rows, list) else []
    sample = [_sample_resource(row) for row in rows[:5]]
    return _check(
        'discover_' + resource,
        'Pobranie: ' + resource,
        'discovery',
        'pass',
        f"Pobrano listę „{resource}” ({len(rows)} elementów).",
        latency_ms=round((monotonic() - started) * 1000),
        details={'resource': resource, 'count': len(rows), 'sample': sample},
    )


def diagnose_provider(provider, credential, *, deep=False):
    checks = []
    targets = _network_targets(credential)
    if not targets:
        checks.append(_check(
            'endpoint',
            'Konfiguracja endpointu',
            'configuration',
            'warn',
            'Provider nie ma jawnego endpointu sieciowego do osobnego testu DNS/TCP. Test API zostanie wykonany przez adapter.',
        ))

    for index, target in enumerate(targets):
        dns = _dns_check(target)
        dns['id'] = f"dns_{index}"
        checks.append(dns)
        ping = _ping_check(target)
        ping['id'] = f"icmp_{index}"
        checks.append(ping)
        tcp = _tcp_check(target)
        tcp['id'] = f"tcp_{index}"
        checks.append(tcp)
        if tcp['status'] == 'pass':
            tls = _tls_check(target)
            tls['id'] = f"tls_{index}"
            checks.append(tls)
            http = _http_check(credential, target)
            http['id'] = f"http_{index}"
            checks.append(http)
        else:
            checks.append(_check(
                f"tls_{index}",
                f"TLS: {target['label']}",
                'tls',
                'skipped',
                'Test TLS pominięto, ponieważ połączenie TCP nie działa.',
            ))
            checks.append(_check(
                f"http_{index}",
                f"HTTP: {target['label']}",
                'http',
                'skipped',
                'Test HTTP pominięto, ponieważ połączenie TCP nie działa.',
            ))

    try:
        adapter = provider_for(credential)
    except HTTPException as exc:
        adapter = None
        auth = _check(
            'provider_auth',
            'Autoryzacja i API',
            'provider',
            'fail',
            _detail_text(exc.detail),
            critical=True,
            details={'http_status': exc.status_code},
        )
    except Exception as exc:
        adapter = None
        auth = _check(
            'provider_auth',
            'Autoryzacja i API',
            'provider',
            'fail',
            'Nie udało się zainicjalizować adaptera providera.',
            critical=True,
            details={'reason': exc.__class__.__name__},
        )
    else:
        auth = _auth_check(adapter, provider.type)
    checks.append(auth)

    if auth['status'] == 'pass' and adapter is not None:
        resources = _DISCOVERY_RESOURCES.get(provider.type, ()) if deep else _QUICK_DISCOVERY.get(provider.type, ())
        for resource in resources:
            checks.append(_discovery_check(adapter, resource))
    else:
        checks.append(_check(
            'discovery',
            'Pobieranie zasobów',
            'discovery',
            'skipped',
            'Testy pobierania zasobów pominięto, ponieważ autoryzacja do API nie działa.',
        ))

    critical_failed = any(item['status'] == 'fail' and item.get('critical') for item in checks)
    optional_failed = any(item['status'] == 'fail' and not item.get('critical') for item in checks)
    warnings = any(item['status'] == 'warn' for item in checks)
    if critical_failed:
        status = 'failed'
    elif optional_failed or warnings:
        status = 'degraded'
    else:
        status = 'ok'

    return {
        'provider': {
            'id': provider.id,
            'name': provider.name,
            'type': provider.type,
            'label': _PROVIDER_LABELS.get(provider.type, provider.type),
            'credentials_id': provider.credentials_id,
            'endpoint': credential.endpoint or None,
            'verify_ssl': bool(credential.verify_ssl),
        },
        'mode': 'deep' if deep else 'quick',
        'status': status,
        'summary': {
            'passed': sum(1 for item in checks if item['status'] == 'pass'),
            'warnings': sum(1 for item in checks if item['status'] == 'warn'),
            'failed': sum(1 for item in checks if item['status'] == 'fail'),
            'skipped': sum(1 for item in checks if item['status'] == 'skipped'),
        },
        'checks': checks,
    }


@router.post('/providers/{provider_id}')
def run_provider_diagnostics(
    provider_id: int,
    request: Request,
    deep: bool = Query(False),
    actor=Depends(require('providers.update')),
    db=Depends(get_db, scope='function'),
):
    provider = find(db, Provider, provider_id)
    require_platform_enabled(db, provider.type)
    credential = find(db, Credential, provider.credentials_id)
    if provider.type != credential.type:
        raise HTTPException(409, 'Provider i przypisane dane dostępowe mają różne typy.')
    result = diagnose_provider(provider, credential, deep=deep)
    audit(
        db,
        request,
        'provider.diagnostics',
        'providers',
        provider_id,
        'failure' if result['status'] == 'failed' else 'success',
    )
    db.commit()
    return result
