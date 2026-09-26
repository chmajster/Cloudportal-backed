"""AWX / Automation Controller integration.

CloudPortal accepts a controller URL plus username/password once, attempts to
exchange those credentials for an OAuth personal access token, and stores only
the resulting encrypted token whenever the controller permits it.  Basic-auth
credentials are retained only as an encrypted fallback for controllers where
personal token creation is disabled.

No AWX secret is ever embedded in Terraform state or cloud-init user-data.
"""

from __future__ import annotations

import json
import re
import yaml
from urllib.parse import urlsplit, urlunsplit

import httpx


class AwxError(RuntimeError):
    pass


DEFAULT_AWX_INVENTORY_PATTERN = '<Projekt>-<APMID>-<ENV>'
_AWX_INVENTORY_TOKEN_RE = re.compile(r'<([^<>]+)>')


def render_awx_inventory_name(
    pattern: str | None,
    *,
    project: str | None = None,
    apmid: str | None = None,
    environment: str | None = None,
) -> str:
    template = str(pattern or DEFAULT_AWX_INVENTORY_PATTERN).strip()
    if not template:
        template = DEFAULT_AWX_INVENTORY_PATTERN

    values = {
        'projekt': str(project or '').strip(),
        'project': str(project or '').strip(),
        'apmid': str(apmid or '').strip().upper(),
        'env': str(environment or '').strip().upper(),
        'environment': str(environment or '').strip().upper(),
    }
    missing = []
    unknown = []

    def replace(match):
        token = str(match.group(1) or '').strip()
        key = token.lower()
        if key not in values:
            unknown.append('<' + token + '>')
            return ''
        value = values[key]
        if not value:
            missing.append('<' + token + '>')
            return ''
        return value

    rendered = _AWX_INVENTORY_TOKEN_RE.sub(replace, template)
    if unknown:
        raise AwxError(
            'Unsupported AWX inventory pattern token(s): ' + ', '.join(sorted(set(unknown)))
        )
    if missing:
        raise AwxError(
            'AWX inventory pattern requires values for: ' + ', '.join(sorted(set(missing)))
        )
    if '<' in rendered or '>' in rendered:
        raise AwxError('AWX inventory pattern contains an invalid token')
    rendered = re.sub(r'\s+', ' ', rendered).strip().strip('-_.')
    if not rendered:
        raise AwxError('AWX inventory pattern rendered an empty name')
    if len(rendered) > 100:
        raise AwxError('Rendered AWX inventory name exceeds 100 characters')
    return rendered


def normalize_awx_endpoint(value: str) -> str:
    raw = str(value or '').strip().rstrip('/')
    if not raw:
        raise AwxError('AWX endpoint is required')
    if '://' not in raw:
        raw = 'https://' + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise AwxError('AWX endpoint must use HTTP or HTTPS')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AwxError('AWX endpoint must not contain credentials, query or fragment')
    path = parsed.path.rstrip('/')
    for suffix in ('/api/controller/v2', '/api/v2'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', '')).rstrip('/')


class AwxClient:
    def __init__(
        self,
        endpoint: str,
        *,
        verify_ssl: bool = True,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 20,
    ):
        self.endpoint = normalize_awx_endpoint(endpoint)
        self.verify_ssl = bool(verify_ssl)
        self.token = str(token or '').strip() or None
        self.username = str(username or '').strip() or None
        self.password = password
        self.timeout = timeout
        self._api_base: str | None = None

    def _client(self) -> httpx.Client:
        headers = {'Accept': 'application/json'}
        auth = None
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        elif self.username is not None and self.password is not None:
            auth = (self.username, self.password)
        return httpx.Client(
            verify=self.verify_ssl,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
            auth=auth,
        )

    def _candidate_bases(self) -> list[str]:
        return [
            self.endpoint + '/api/v2',
            self.endpoint + '/api/controller/v2',
        ]

    def api_base(self) -> str:
        if self._api_base:
            return self._api_base
        last_status = None
        with self._client() as client:
            for base in self._candidate_bases():
                try:
                    response = client.get(base + '/ping/')
                except httpx.HTTPError:
                    continue
                last_status = response.status_code
                if response.status_code == 200:
                    self._api_base = base
                    return base
                if response.status_code in {401, 403}:
                    raise AwxError('AWX authentication failed')
        if last_status is not None:
            raise AwxError(f'AWX API discovery failed (HTTP {last_status})')
        raise AwxError('AWX API is unreachable')

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = self.api_base() + '/' + path.lstrip('/')
        try:
            with self._client() as client:
                response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise AwxError('AWX API request failed') from exc
        if response.status_code in {401, 403}:
            raise AwxError('AWX authentication or authorization failed')
        if response.status_code >= 400:
            detail = ''
            try:
                payload = response.json()
                detail = str(payload.get('detail') or payload.get('error') or '')
            except Exception:
                detail = ''
            suffix = f': {detail[:300]}' if detail else ''
            raise AwxError(f'AWX API returned HTTP {response.status_code}{suffix}')
        return response

    def list_resource(self, resource: str, *, params: dict | None = None) -> list[dict]:
        query = {'page_size': 200}
        if params:
            query.update(params)
        rows = []
        page = 1
        while page <= 100:
            page_query = dict(query)
            page_query['page'] = page
            payload = self.request(
                'GET',
                resource.rstrip('/') + '/',
                params=page_query,
            ).json()
            if not isinstance(payload, dict):
                break
            rows.extend(row for row in (payload.get('results') or []) if isinstance(row, dict))
            if not payload.get('next'):
                break
            page += 1
        return rows

    def current_user(self) -> dict:
        payload = self.request('GET', 'me/').json()
        if isinstance(payload, dict):
            results = payload.get('results')
            if isinstance(results, list) and results:
                return dict(results[0])
            if payload.get('id'):
                return payload
        raise AwxError('AWX did not return the authenticated user')

    def discovery(self) -> dict:
        ping = self.request('GET', 'ping/').json()
        user = self.current_user()
        inventories = self.list_resource('inventories')
        job_templates = self.list_resource('job_templates')
        organizations = self.list_resource('organizations')
        projects = self.list_resource('projects')
        version = ''
        if isinstance(ping, dict):
            version = str(
                ping.get('version')
                or ping.get('active_node_installation_uuid')
                or ''
            )
        return {
            'ok': True,
            'endpoint': self.endpoint,
            'api_base': self.api_base(),
            'version': version or None,
            'user': {
                'id': user.get('id'),
                'username': user.get('username') or self.username,
            },
            'inventories': [
                {'id': row.get('id'), 'name': row.get('name'), 'organization': row.get('organization')}
                for row in inventories if row.get('id') and row.get('name')
            ],
            'job_templates': [
                {
                    'id': row.get('id'),
                    'name': row.get('name'),
                    'inventory': row.get('inventory'),
                    'project': row.get('project'),
                    'playbook': row.get('playbook'),
                    'execution_environment': row.get('execution_environment'),
                }
                for row in job_templates if row.get('id') and row.get('name')
            ],
            'organizations': [
                {'id': row.get('id'), 'name': row.get('name')}
                for row in organizations if row.get('id') and row.get('name')
            ],
            'projects': [
                {
                    'id': row.get('id'),
                    'name': row.get('name'),
                    'organization': row.get('organization'),
                    'status': row.get('status'),
                    'scm_type': row.get('scm_type'),
                }
                for row in projects if row.get('id') and row.get('name')
            ],
        }

    def create_personal_token(self, description: str = 'CloudPortal') -> dict:
        user = self.current_user()
        payload = {'description': description[:150], 'scope': 'write', 'application': None}
        paths = ['tokens/']
        if user.get('id'):
            paths.append(f"users/{int(user['id'])}/personal_tokens/")
        last_error = None
        for path in paths:
            try:
                response = self.request('POST', path, json=payload)
            except AwxError as exc:
                last_error = exc
                continue
            data = response.json()
            token = str(data.get('token') or '').strip() if isinstance(data, dict) else ''
            if token:
                return {'token': token, 'token_id': str(data.get('id') or '') or None}
        raise last_error or AwxError('AWX did not issue an OAuth token')

    def _organization_for_inventory(self) -> int:
        organizations = self.list_resource('organizations')
        if not organizations:
            raise AwxError('No AWX organization is available for automatic inventory creation')
        preferred = next(
            (row for row in organizations if str(row.get('name') or '').lower() == 'default'),
            None,
        )
        selected = preferred or sorted(
            organizations,
            key=lambda row: (str(row.get('name') or '').lower(), int(row.get('id') or 0)),
        )[0]
        return int(selected['id'])

    def ensure_inventory(
        self,
        *,
        inventory_id: int | None = None,
        name: str = 'CloudPortal',
        organization_id: int | None = None,
    ) -> dict:
        selected_organization_id = int(organization_id) if organization_id else None
        if inventory_id:
            data = self.request('GET', f'inventories/{int(inventory_id)}/').json()
            if not isinstance(data, dict) or not data.get('id'):
                raise AwxError('Selected AWX inventory is unavailable')
            if selected_organization_id and int(data.get('organization') or 0) != selected_organization_id:
                raise AwxError('Selected AWX inventory does not belong to the selected organization')
            return data

        lookup = {'name': name}
        if selected_organization_id:
            lookup['organization'] = selected_organization_id
        existing = self.list_resource('inventories', params=lookup)
        if existing:
            return existing[0]

        target_organization_id = selected_organization_id or self._organization_for_inventory()
        try:
            data = self.request('POST', 'inventories/', json={
                'name': name,
                'description': 'Managed automatically by CloudPortal',
                'organization': target_organization_id,
            }).json()
            if isinstance(data, dict) and data.get('id'):
                return data
        except AwxError:
            # Never silently redirect onboarding into an unrelated inventory just
            # because it is currently the only visible one.
            retry = {'name': name, 'organization': target_organization_id}
            matches = self.list_resource('inventories', params=retry)
            if matches:
                return matches[0]
            raise
        raise AwxError('AWX inventory creation did not return an inventory')

    def ensure_host(
        self,
        *,
        inventory_id: int,
        hostname: str,
        ansible_host: str,
        variables: dict,
    ) -> dict:
        existing = self.list_resource(
            'hosts',
            params={'inventory': int(inventory_id), 'name': hostname},
        )
        merged_variables = dict(variables)
        if existing:
            raw_existing = existing[0].get('variables')
            if isinstance(raw_existing, str) and raw_existing.strip():
                try:
                    parsed = yaml.safe_load(raw_existing)
                except yaml.YAMLError:
                    parsed = None
                if isinstance(parsed, dict):
                    merged_variables = {**parsed, **variables}
        payload = {
            'name': hostname,
            'description': 'Managed automatically by CloudPortal',
            'enabled': True,
            'variables': json.dumps(merged_variables, sort_keys=True),
        }
        if existing:
            host_id = int(existing[0]['id'])
            data = self.request('PATCH', f'hosts/{host_id}/', json=payload).json()
            return data if isinstance(data, dict) else existing[0]
        data = self.request(
            'POST',
            f'inventories/{int(inventory_id)}/hosts/',
            json=payload,
        ).json()
        if not isinstance(data, dict) or not data.get('id'):
            raise AwxError('AWX host creation did not return a host')
        return data

    def ensure_group(self, *, inventory_id: int, name: str) -> dict:
        existing = self.list_resource(
            'groups',
            params={'inventory': int(inventory_id), 'name': name},
        )
        if existing:
            return existing[0]
        data = self.request(
            'POST',
            f'inventories/{int(inventory_id)}/groups/',
            json={'name': name, 'description': 'Managed automatically by CloudPortal'},
        ).json()
        if not isinstance(data, dict) or not data.get('id'):
            raise AwxError('AWX group creation did not return a group')
        return data

    def add_host_to_group(self, *, group_id: int, host_id: int) -> None:
        self.request('POST', f'groups/{int(group_id)}/hosts/', json={'id': int(host_id)})

    def register_host(
        self,
        *,
        hostname: str,
        ansible_host: str,
        deployment_id: str,
        environment: str | None = None,
        apmid: str | None = None,
        inventory_id: int | None = None,
        inventory_name: str = 'CloudPortal',
        organization_id: int | None = None,
        group_by_environment: bool = True,
        group_by_apmid: bool = True,
    ) -> dict:
        inventory = self.ensure_inventory(
            inventory_id=inventory_id,
            name=inventory_name,
            organization_id=organization_id,
        )
        variables = {
            'ansible_host': ansible_host,
            'cloudportal_managed': True,
            'cloudportal_deployment_id': deployment_id,
        }
        if environment:
            variables['environment'] = environment
        if apmid:
            variables['apmid'] = apmid

        host = self.ensure_host(
            inventory_id=int(inventory['id']),
            hostname=hostname,
            ansible_host=ansible_host,
            variables=variables,
        )
        groups = []
        if group_by_environment and environment:
            groups.append('env-' + str(environment).lower())
        if group_by_apmid and apmid:
            groups.append('apmid-' + str(apmid).lower())
        for group_name in groups:
            group = self.ensure_group(inventory_id=int(inventory['id']), name=group_name)
            self.add_host_to_group(group_id=int(group['id']), host_id=int(host['id']))
        return {
            'inventory_id': int(inventory['id']),
            'inventory_name': inventory.get('name'),
            'host_id': int(host['id']),
            'host_name': host.get('name') or hostname,
            'groups': groups,
        }

    def remove_host(
        self,
        *,
        hostname: str,
        inventory_id: int | None = None,
        inventory_name: str = 'CloudPortal',
        organization_id: int | None = None,
    ) -> dict:
        inventory = None
        if inventory_id:
            try:
                candidate = self.request('GET', f'inventories/{int(inventory_id)}/').json()
            except AwxError:
                candidate = None
            if isinstance(candidate, dict) and candidate.get('id'):
                if organization_id and int(candidate.get('organization') or 0) != int(organization_id):
                    candidate = None
                inventory = candidate
        else:
            lookup = {'name': inventory_name}
            if organization_id:
                lookup['organization'] = int(organization_id)
            matches = self.list_resource('inventories', params=lookup)
            if matches:
                inventory = matches[0]

        if not inventory:
            return {'removed': False, 'reason': 'inventory_not_found'}

        hosts = self.list_resource(
            'hosts',
            params={'inventory': int(inventory['id']), 'name': hostname},
        )
        removed = []
        for host in hosts:
            if str(host.get('name') or '') != hostname or not host.get('id'):
                continue
            host_id = int(host['id'])
            self.request('DELETE', f'hosts/{host_id}/')
            removed.append(host_id)
        return {
            'removed': bool(removed),
            'host_ids': removed,
            'inventory_id': int(inventory['id']),
        }

    def launch_job_template(
        self,
        template_id: int,
        *,
        hostname: str,
        deployment_id: str,
        environment: str | None = None,
        apmid: str | None = None,
    ) -> dict:
        extra_vars = {
            'cloudportal_onboarding': True,
            'cloudportal_deployment_id': deployment_id,
        }
        if environment:
            extra_vars['environment'] = str(environment).lower()
        if apmid:
            extra_vars['apmid'] = str(apmid).upper()
        data = self.request(
            'POST',
            f'job_templates/{int(template_id)}/launch/',
            json={
                'limit': hostname,
                'extra_vars': extra_vars,
            },
        ).json()
        return data if isinstance(data, dict) else {}


def test_awx_connection(
    endpoint: str,
    username: str,
    secret: dict,
    *,
    verify_ssl: bool = True,
) -> dict:
    client = AwxClient(
        endpoint,
        verify_ssl=verify_ssl,
        token=secret.get('token'),
        username=username,
        password=secret.get('password'),
    )
    discovery = client.discovery()
    return {
        'ok': True,
        'provider': 'awx',
        'version': discovery.get('version'),
        'endpoint': discovery.get('endpoint'),
        'username': (discovery.get('user') or {}).get('username') or username,
        'auth_mode': 'token' if secret.get('token') else 'password',
        'verify_ssl': bool(verify_ssl),
        'message': (
            'AWX connection works; '
            f"{len(discovery.get('inventories') or [])} inventories and "
            f"{len(discovery.get('job_templates') or [])} job templates discovered"
        ),
    }


def bootstrap_awx(
    endpoint: str,
    username: str,
    password: str,
    *,
    verify_ssl: bool = True,
) -> tuple[str, dict, dict]:
    endpoint = normalize_awx_endpoint(endpoint)
    basic = AwxClient(
        endpoint,
        verify_ssl=verify_ssl,
        username=username,
        password=password,
    )
    discovery = basic.discovery()
    try:
        issued = basic.create_personal_token()
    except AwxError:
        # Some controller deployments disable PAT creation. Keep the working
        # password only as an encrypted fallback rather than failing setup.
        return endpoint, {'password': password}, discovery

    secret = {'token': issued['token']}
    if issued.get('token_id'):
        secret['token_id'] = issued['token_id']
    token_client = AwxClient(endpoint, verify_ssl=verify_ssl, token=issued['token'])
    token_client.discovery()
    return endpoint, secret, discovery
