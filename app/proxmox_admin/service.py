from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.day2.diff import redact
from app.day2.models import Day2ActionRequest
from app.day2.service import acquire_resource_lock, day2_settings, release_resource_lock
from app.models import (
    Audit,
    Credential,
    Job,
    JobLog,
    ManagedResource,
    ManagedVM,
    Provider,
    Setting,
    User,
    now,
)
from app.operations.service import queue_job_webhooks, queue_webhook_event
from app.policy_engine.service import evaluate_context
from app.proxmox_admin.reconciliation import reconcile
from app.projects.models import Project
from app.providers.registry import provider_for
from app.resource_scope.authorization import DEFAULT_SCOPE
from app.resource_scope.database import row_scope
from app.resource_scope.service import filter_provider_vms
from app.security.core import audit
from app.tenancy.models import Tenant


SETTING_KEY = 'proxmox_admin'
DEFAULT_SETTINGS = {'enabled': False}
TERMINAL_JOB_STATUSES = {'successful', 'failed', 'cancelled', 'reconciliation_required'}
ACTIVE_JOB_STATUSES = {'queued', 'running', 'cancelling', 'waiting_approval', 'reconciliation_required'}

COMMAND_PERMISSIONS = {
    'vm.power': 'proxmox_admin.vm.power',
    'vm.config': 'proxmox_admin.vm.modify',
    'vm.cloudinit': 'proxmox_admin.vm.modify',
    'vm.clone': 'proxmox_admin.vm.clone',
    'vm.migrate': 'proxmox_admin.vm.migrate',
    'vm.template': 'proxmox_admin.templates.manage',
    'vm.delete': 'proxmox_admin.vm.delete',
    'vm.disk.resize': 'proxmox_admin.vm.modify',
    'vm.disk.add': 'proxmox_admin.vm.modify',
    'vm.disk.remove': 'proxmox_admin.vm.modify',
    'vm.disk.move': 'proxmox_admin.vm.modify',
    'vm.nic.set': 'proxmox_admin.vm.modify',
    'vm.nic.remove': 'proxmox_admin.vm.modify',
    'vm.snapshot.create': 'proxmox_admin.snapshots.manage',
    'vm.snapshot.delete': 'proxmox_admin.snapshots.manage',
    'vm.snapshot.rollback': 'proxmox_admin.snapshots.manage',
    'lxc.power': 'proxmox_admin.containers.power',
    'lxc.config': 'proxmox_admin.containers.modify',
    'lxc.clone': 'proxmox_admin.containers.clone',
    'lxc.migrate': 'proxmox_admin.containers.migrate',
    'lxc.delete': 'proxmox_admin.containers.delete',
    'lxc.snapshot.create': 'proxmox_admin.snapshots.manage',
    'lxc.snapshot.delete': 'proxmox_admin.snapshots.manage',
    'lxc.snapshot.rollback': 'proxmox_admin.snapshots.manage',
    'backup.run': 'proxmox_admin.backups.run',
    'backup.restore': 'proxmox_admin.backups.restore',
    'backup.delete': 'proxmox_admin.backups.delete',
    'node.service': 'proxmox_admin.nodes.services.manage',
    'firewall.rule.create': 'proxmox_admin.firewall.manage',
    'firewall.rule.update': 'proxmox_admin.firewall.manage',
    'firewall.rule.delete': 'proxmox_admin.firewall.manage',
}

DESTRUCTIVE_COMMANDS = {
    'vm.delete',
    'vm.disk.remove',
    'vm.snapshot.rollback',
    'lxc.delete',
    'lxc.snapshot.rollback',
    'backup.restore',
    'backup.delete',
    'node.service',
    'firewall.rule.create',
    'firewall.rule.update',
    'firewall.rule.delete',
}

GLOBAL_NATIVE_MUTATIONS = {
    'node.service',
    'backup.restore',
    'backup.delete',
    'firewall.rule.create',
    'firewall.rule.update',
    'firewall.rule.delete',
}


def proxmox_admin_settings(db):
    row = db.get(Setting, SETTING_KEY)
    value = dict(DEFAULT_SETTINGS)
    if row is not None and isinstance(row.value, dict):
        value.update({key: row.value[key] for key in DEFAULT_SETTINGS if key in row.value})
    value['enabled'] = bool(value.get('enabled', False))
    return value


def save_proxmox_admin_settings(db, values):
    value = {'enabled': bool(values.get('enabled', False))}
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value


def ensure_enabled(db):
    if not proxmox_admin_settings(db)['enabled']:
        raise HTTPException(404, {'code': 'PROXMOX_ADMIN_DISABLED', 'message': 'Proxmox Admin is disabled'})
    return True


def request_permissions(request):
    return set(getattr(request.state, 'permissions', set()) or set())


def has_global_native_access(request):
    return 'proxmox_admin.scope.all' in request_permissions(request)


def get_proxmox_provider(db, provider_id):
    provider = db.get(Provider, int(provider_id))
    if provider is None or provider.type != 'proxmox':
        raise HTTPException(404, {'code': 'PROVIDER_NOT_FOUND', 'message': 'Proxmox provider not found'})
    credential = db.get(Credential, provider.credentials_id)
    if credential is None or credential.type != 'proxmox':
        raise HTTPException(409, {
            'code': 'PROVIDER_CREDENTIAL_UNAVAILABLE',
            'message': 'Proxmox provider has no usable Proxmox credential',
        })
    return provider, credential, provider_for(credential)


def provider_public(provider):
    return {
        'id': provider.id,
        'name': provider.name,
        'type': provider.type,
        'credentials_id': provider.credentials_id,
        'updated_at': provider.updated_at.isoformat() + 'Z' if provider.updated_at else None,
    }


def list_proxmox_providers(db):
    rows = db.scalars(
        select(Provider).where(Provider.type == 'proxmox').order_by(Provider.name.asc(), Provider.id.asc())
    ).all()
    return [provider_public(row) for row in rows]


def fetched(payload):
    return {
        **payload,
        'fetched_at': now().isoformat() + 'Z',
        'source': 'proxmox-live',
    }


def _tags(value):
    if isinstance(value, str):
        return [item.strip() for item in value.replace(',', ';').split(';') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _classification(tags):
    apmid = None
    environment = None
    for tag in tags:
        text = str(tag).strip()
        lower = text.lower()
        if lower.startswith('apmid-') and len(text) > 6 and apmid is None:
            apmid = text[6:].upper()
        elif lower.startswith('env-') and lower[4:] in {'dev', 'test', 'nonprod', 'prod'} and environment is None:
            environment = lower[4:]
        elif '.' in text and apmid is None:
            left, right = text.rsplit('.', 1)
            if left and right.lower() in {'dev', 'test', 'nonprod', 'prod'}:
                apmid = left.upper()
                environment = environment or right.lower()
    return apmid, environment


def _ownership_by_vmid(db, provider_id, vmids):
    vmids = sorted({int(value) for value in vmids})
    if not vmids:
        return {}
    vm = ManagedVM.__table__
    resource = ManagedResource.__table__
    rows = db.connection().execute(
        select(
            vm.c.vm_id,
            vm.c.id,
            vm.c.tenant_id,
            vm.c.project_id,
            vm.c.deployment_id,
            vm.c.management_mode,
            resource.c.primary_ip,
            resource.c.metadata_json,
        ).select_from(
            vm.outerjoin(resource, resource.c.deployment_id == vm.c.deployment_id)
        ).where(vm.c.provider_id == int(provider_id), vm.c.vm_id.in_(vmids))
    ).all()
    return {
        int(row.vm_id): {
            'managed_vm_id': row.id,
            'tenant_id': row.tenant_id,
            'project_id': row.project_id,
            'deployment_id': row.deployment_id,
            'management_mode': row.management_mode,
            'primary_ip': row.primary_ip,
            'metadata': dict(row.metadata_json or {}),
        }
        for row in rows
    }


def _scope_names(db, ownership):
    keys = {(item.get('tenant_id'), item.get('project_id')) for item in ownership.values() if item.get('project_id')}
    result = {}
    tenant_table = Tenant.__table__
    project_table = Project.__table__
    for tenant_id, project_id in keys:
        row = db.connection().execute(
            select(tenant_table.c.name, project_table.c.name)
            .select_from(project_table.join(tenant_table, tenant_table.c.id == project_table.c.tenant_id))
            .where(project_table.c.id == project_id, project_table.c.tenant_id == tenant_id)
        ).one_or_none()
        if row:
            result[(tenant_id, project_id)] = {'organization': row[0], 'project': row[1]}
    return result


def _visible_native_rows(db, provider_id, rows, request):
    if has_global_native_access(request):
        return list(rows)
    return filter_provider_vms(db, int(provider_id), list(rows))


def _inventory_rows(db, provider_id, request, kind):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    rows = adapter.cluster_resources('vm')
    wanted = 'qemu' if kind == 'vm' else 'lxc'
    rows = [dict(row) for row in rows if row.get('type') == wanted and not (wanted == 'qemu' and bool(row.get('template')))]
    return _visible_native_rows(db, provider_id, rows, request)


def _enrich_inventory(db, provider_id, rows):
    ownership = _ownership_by_vmid(db, provider_id, [row.get('vmid') for row in rows if row.get('vmid') is not None])
    scope_names = _scope_names(db, ownership)
    result = []
    for row in rows:
        item = dict(row)
        vmid = int(item['vmid']) if item.get('vmid') is not None else None
        owner = ownership.get(vmid, {})
        metadata = dict(owner.get('metadata') or {})
        tags = _tags(item.get('tags') or metadata.get('tags'))
        tag_apmid, tag_env = _classification(tags)
        scope = scope_names.get((owner.get('tenant_id'), owner.get('project_id')), {})
        item.update({
            'managed': bool(owner.get('deployment_id')) and owner.get('management_mode') != 'external',
            'management_mode': owner.get('management_mode') or 'unmanaged',
            'managed_vm_id': owner.get('managed_vm_id'),
            'deployment_id': owner.get('deployment_id'),
            'organization_id': owner.get('tenant_id'),
            'project_id': owner.get('project_id'),
            'organization': scope.get('organization'),
            'project': scope.get('project'),
            'apmid': metadata.get('apmid') or tag_apmid,
            'environment': metadata.get('environment') or tag_env,
            'ip': owner.get('primary_ip'),
            'tags_list': tags,
            'last_sync': None,
        })
        result.append(item)
    return result


def dashboard(db, provider_id, request):
    provider, _, adapter = get_proxmox_provider(db, provider_id)
    cluster = adapter.cluster_status()
    resources = adapter.cluster_resources()
    nodes = [row for row in resources if row.get('type') == 'node']
    qemu = [row for row in resources if row.get('type') == 'qemu' and not row.get('template')]
    lxc = [row for row in resources if row.get('type') == 'lxc']
    storage = [row for row in resources if row.get('type') == 'storage']
    visible_qemu = _visible_native_rows(db, provider_id, qemu, request)
    visible_lxc = _visible_native_rows(db, provider_id, lxc, request)

    task_rows = []
    for node in nodes[:64]:
        if node.get('status') != 'online':
            continue
        try:
            task_rows.extend(adapter.node_tasks(node['node'], limit=50))
        except HTTPException:
            continue
    running_tasks = [row for row in task_rows if not row.get('endtime')]
    errors = [
        row for row in task_rows
        if row.get('endtime') and str(row.get('status') or row.get('exitstatus') or '').upper() not in {'OK', ''}
    ][:20]
    migrations = [
        row for row in task_rows
        if any(word in str(row.get('type') or row.get('upid') or '').lower() for word in ('migrate', 'qmigrate', 'vzmigrate'))
    ][:20]
    cluster_row = next((row for row in cluster if row.get('type') == 'cluster'), {})
    try:
        backup_jobs = adapter.backup_jobs()
    except HTTPException:
        backup_jobs = []
    try:
        ha = adapter.cluster_ha_status()
    except HTTPException:
        ha = []

    return fetched({
        'provider': provider_public(provider),
        'cluster': {
            'name': cluster_row.get('name') or provider.name,
            'version': (adapter.test() or {}).get('version'),
            'quorate': cluster_row.get('quorate'),
            'nodes': len(nodes),
            'nodes_online': sum(1 for row in nodes if row.get('status') == 'online'),
            'nodes_offline': sum(1 for row in nodes if row.get('status') != 'online'),
            'ha': ha,
            'ceph': adapter.ceph_status(),
        },
        'compute': {
            'vms': len(visible_qemu),
            'lxc': len(visible_lxc),
            'running': sum(1 for row in [*visible_qemu, *visible_lxc] if row.get('status') == 'running'),
            'stopped': sum(1 for row in [*visible_qemu, *visible_lxc] if row.get('status') != 'running'),
            'cpu_total': sum(float(row.get('maxcpu') or 0) for row in nodes),
            'cpu_used_ratio': (
                sum(float(row.get('cpu') or 0) * float(row.get('maxcpu') or 0) for row in nodes)
                / max(1.0, sum(float(row.get('maxcpu') or 0) for row in nodes))
            ),
            'ram_total': sum(int(row.get('maxmem') or 0) for row in nodes),
            'ram_used': sum(int(row.get('mem') or 0) for row in nodes),
        },
        'storage': {
            'items': len(storage),
            'total': sum(int(row.get('maxdisk') or 0) for row in storage),
            'used': sum(int(row.get('disk') or 0) for row in storage),
            'available': sum(max(0, int(row.get('maxdisk') or 0) - int(row.get('disk') or 0)) for row in storage),
        },
        'tasks': {
            'active': len(running_tasks),
            'errors': errors,
            'last_migrations': migrations,
        },
        'backups': {'jobs': len(backup_jobs), 'items': backup_jobs[:50]},
    })


def list_nodes(db, provider_id):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    resources = {row.get('node'): row for row in adapter.cluster_resources('node') if row.get('node')}
    items = []
    for node_name, row in resources.items():
        status = {}
        version = {}
        try:
            status = adapter.node_status(node_name) or {}
            version = adapter.node_version(node_name) or {}
        except HTTPException:
            pass
        items.append({
            **dict(row),
            'kernel': version.get('release') or version.get('repoid'),
            'pve_version': version.get('version'),
            'loadavg': status.get('loadavg'),
            'ha_state': row.get('hastate') or row.get('ha'),
            'ip': status.get('pveversion') and status.get('kversion') and row.get('ip') or row.get('ip'),
            'last_refresh': now().isoformat() + 'Z',
        })
    return fetched({'items': sorted(items, key=lambda item: str(item.get('node') or ''))})


def node_detail(db, provider_id, node):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    status = adapter.node_status(node)
    version = adapter.node_version(node)
    return fetched({
        'node': node,
        'overview': {**(status or {}), **{'version': version or {}}},
        'network': adapter.node_network(node),
        'dns': adapter.node_dns(node),
        'subscription': adapter.node_subscription(node),
        'repositories': adapter.node_repositories(node),
        'services': adapter.node_services(node),
    })


def node_syslog(db, provider_id, node, *, since=None, until=None, limit=500, severity=None, search=None):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    rows = adapter.node_syslog(node, since=since, until=until, limit=limit)
    if severity:
        severity_lower = severity.lower()
        rows = [row for row in rows if severity_lower in str(row.get('pri') or row.get('severity') or '').lower()]
    if search:
        query = search.lower()
        rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False).lower()]
    return fetched({'items': rows})


def list_vms(db, provider_id, request):
    rows = _inventory_rows(db, provider_id, request, 'vm')
    return fetched({'items': _enrich_inventory(db, provider_id, rows)})


def list_containers(db, provider_id, request):
    rows = _inventory_rows(db, provider_id, request, 'lxc')
    return fetched({'items': _enrich_inventory(db, provider_id, rows)})


def _ensure_visible_object(db, provider_id, request, node, object_id, kind):
    rows = _inventory_rows(db, provider_id, request, 'vm' if kind == 'vm' else 'lxc')
    match = next((
        row for row in rows
        if int(row.get('vmid', -1)) == int(object_id) and str(row.get('node') or '') == str(node)
    ), None)
    if match is None:
        raise HTTPException(404, {'code': 'RESOURCE_NOT_FOUND', 'message': 'Proxmox resource not found'})
    return match


def vm_detail(db, provider_id, request, node, vmid):
    base = _ensure_visible_object(db, provider_id, request, node, vmid, 'vm')
    _, _, adapter = get_proxmox_provider(db, provider_id)
    status = adapter.vm_status(node, vmid) or {}
    config = adapter.vm_config(node, vmid) or {}
    try:
        addresses = adapter.guest_addresses(node, vmid)
    except HTTPException:
        addresses = []
    enriched = _enrich_inventory(db, provider_id, [base])[0]
    return fetched({
        'overview': {**enriched, **status, 'addresses': addresses},
        'hardware': redact(config),
        'raw': redact({'status': status, 'config': config}),
    })


def container_detail(db, provider_id, request, node, vmid):
    base = _ensure_visible_object(db, provider_id, request, node, vmid, 'lxc')
    _, _, adapter = get_proxmox_provider(db, provider_id)
    status = adapter.lxc_status(node, vmid) or {}
    config = adapter.lxc_config(node, vmid) or {}
    enriched = _enrich_inventory(db, provider_id, [base])[0]
    return fetched({
        'overview': {**enriched, **status},
        'configuration': redact(config),
        'raw': redact({'status': status, 'config': config}),
    })


def list_storage(db, provider_id):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    configs = {str(row.get('storage')): dict(row) for row in adapter.discover('storages') if row.get('storage')}
    resources = [dict(row) for row in adapter.cluster_resources('storage')]
    items = []
    for row in resources:
        config = configs.get(str(row.get('storage')), {})
        item = {
            **row,
            'storage_type': config.get('type') or row.get('plugintype'),
            'content': config.get('content') or row.get('content'),
            'nodes': config.get('nodes'),
            'shared': bool(config.get('shared') or row.get('shared')),
            'enabled': not bool(config.get('disable')) and bool(row.get('status', 'available') != 'unavailable'),
            'free': max(0, int(row.get('maxdisk') or 0) - int(row.get('disk') or 0)),
        }
        total = int(row.get('maxdisk') or 0)
        item['usage_ratio'] = (int(row.get('disk') or 0) / total) if total else None
        items.append(item)
    return fetched({'items': items})


def storage_content(db, provider_id, node, storage, content=None):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    return fetched({
        'status': adapter.storage_status(node, storage),
        'items': adapter.storage_content(node, storage, content=content),
    })


def list_templates(db, provider_id, request):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    rows = [
        dict(row) for row in adapter.cluster_resources('vm')
        if row.get('type') == 'qemu' and bool(row.get('template'))
    ]
    rows = _visible_native_rows(db, provider_id, rows, request)
    return fetched({'items': _enrich_inventory(db, provider_id, rows)})


def list_snapshots(db, provider_id, request):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    items = []
    for kind, rows in (
        ('vm', _inventory_rows(db, provider_id, request, 'vm')),
        ('container', _inventory_rows(db, provider_id, request, 'lxc')),
    ):
        for row in rows[:500]:
            node = row.get('node')
            vmid = row.get('vmid')
            try:
                snapshots = adapter.snapshots(node, vmid) if kind == 'vm' else adapter.lxc_snapshots(node, vmid)
            except HTTPException:
                continue
            for snap in snapshots:
                if snap.get('name') == 'current':
                    continue
                items.append({
                    **dict(snap),
                    'resource_type': kind,
                    'vmid': vmid,
                    'vm': row.get('name'),
                    'node': node,
                })
    return fetched({'items': items})


def list_tasks(db, provider_id, *, limit=200, errors=False):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    items = []
    nodes = [row for row in adapter.cluster_resources('node') if row.get('node')]
    per_node = max(1, min(200, int(limit)))
    for node in nodes:
        try:
            for row in adapter.node_tasks(node['node'], limit=per_node, errors=errors):
                items.append({**dict(row), 'node': row.get('node') or node['node']})
        except HTTPException:
            continue
    items.sort(key=lambda row: int(row.get('starttime') or 0), reverse=True)
    return fetched({'items': items[:limit]})


def task_detail(db, provider_id, node, upid):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    return fetched({
        'status': adapter.task_status(node, upid),
        'log': adapter.task_log(node, upid, start=0, limit=5000),
    })


def list_backups(db, provider_id):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    jobs = adapter.backup_jobs()
    resources = [row for row in adapter.cluster_resources('storage') if row.get('storage')]
    seen = set()
    backups = []
    for row in resources:
        node = row.get('node')
        storage = row.get('storage')
        key = (node, storage)
        if not node or not storage or key in seen:
            continue
        seen.add(key)
        try:
            content = adapter.storage_content(node, storage, content='backup')
        except HTTPException:
            continue
        backups.extend([{**dict(item), 'node': node, 'storage': storage} for item in content])
    return fetched({'jobs': jobs, 'items': backups})


def cluster_detail(db, provider_id):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    return fetched({
        'status': adapter.cluster_status(),
        'ha': {
            'status': adapter.cluster_ha_status(),
            'groups': adapter.cluster_ha_groups(),
            'resources': adapter.cluster_ha_resources(),
        },
        'ceph': adapter.ceph_status(),
    })


def firewall_snapshot(db, provider_id, level, *, node=None, object_id=None):
    _, _, adapter = get_proxmox_provider(db, provider_id)
    return fetched({
        'level': level,
        'node': node,
        'object_id': object_id,
        'configuration': redact(adapter.firewall_snapshot(level, node=node, object_id=object_id)),
    })


def search(db, provider_id, request, query):
    needle = str(query or '').strip().lower()
    if not needle:
        return fetched({'items': []})
    result = []
    vms = _enrich_inventory(db, provider_id, _inventory_rows(db, provider_id, request, 'vm'))
    containers = _enrich_inventory(db, provider_id, _inventory_rows(db, provider_id, request, 'lxc'))
    for kind, rows in (('vm', vms), ('container', containers)):
        for row in rows:
            haystack = ' '.join(str(row.get(key) or '') for key in (
                'vmid', 'name', 'node', 'tags', 'ip', 'apmid', 'environment',
            )).lower()
            if needle in haystack:
                result.append({'type': kind, **row})
    _, _, adapter = get_proxmox_provider(db, provider_id)
    for row in adapter.cluster_resources():
        if row.get('type') not in {'node', 'storage'}:
            continue
        haystack = json.dumps(row, ensure_ascii=False).lower()
        if needle in haystack:
            result.append({'type': row.get('type'), **dict(row)})
    return fetched({'items': result[:200]})


def _resource_key(provider_id, object_type, object_id):
    return f'proxmox:{int(provider_id)}:{object_type}:{object_id}'


def _lock_resource_id(resource_key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'cloudportal:' + resource_key))


def _target_snapshot(adapter, object_type, node, object_id):
    if object_type == 'vm':
        return redact({
            'status': adapter.vm_status(node, int(object_id)),
            'config': adapter.vm_config(node, int(object_id)),
        })
    if object_type == 'container':
        return redact({
            'status': adapter.lxc_status(node, int(object_id)),
            'config': adapter.lxc_config(node, int(object_id)),
        })
    if object_type == 'node':
        return redact({'status': adapter.node_status(node), 'services': adapter.node_services(node)})
    return {}


def _policy_scope(db, scope, *, apmid=None, environment=None):
    tenant = db.get(Tenant, scope.tenant_id)
    project = db.get(Project, scope.project_id)
    organization = getattr(tenant, 'name', None)
    project_name = getattr(project, 'name', None)
    parts = [organization, project_name, apmid, environment]
    key = '-'.join(str(part) for part in parts if part)
    return {
        'tenant_id': scope.tenant_id,
        'project_id': scope.project_id,
        'organization': organization,
        'project': project_name,
        'apmid': apmid,
        'environment': environment,
        'key': key or None,
    }


def evaluate_operation_policy(db, actor, permissions, scope, command, resource, parameters, *, phase):
    user = db.get(User, actor.user_id)
    tags = _tags(resource.get('tags'))
    apmid, environment = _classification(tags)
    scope_context = _policy_scope(
        db, scope,
        apmid=resource.get('apmid') or apmid,
        environment=resource.get('environment') or environment,
    )
    context = {
        'actor': {
            'id': actor.user_id,
            'username': getattr(user, 'username', ''),
            'roles': sorted(role.name for role in (getattr(user, 'roles', []) or [])),
            'permissions': sorted(permissions),
        },
        'scope': scope_context,
        'request': {
            'action': 'proxmox_admin.' + command,
            'source': 'Worker' if phase == 'worker_revalidate' else 'API',
            'phase': phase,
            'parameters': copy.deepcopy(parameters),
        },
        'resource': {
            **copy.deepcopy(resource),
            'provider_type': 'proxmox',
            'tags': tags,
            'apmid': resource.get('apmid') or apmid,
            'environment': resource.get('environment') or environment,
            'organization': scope_context.get('organization'),
            'project': scope_context.get('project'),
            'scope_key': scope_context.get('key'),
        },
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    if result.get('decision') == 'deny':
        raise HTTPException(403, {
            'code': 'POLICY_DENIED',
            'message': 'Operation denied by CloudPortal Policy Engine',
            'violations': result.get('violations') or [],
            'matched_policy_ids': result.get('matched_policy_ids') or [],
            'decision_id': result.get('decision_id'),
        })
    if result.get('decision') == 'approval_required':
        raise HTTPException(409, {
            'code': 'POLICY_APPROVAL_REQUIRED',
            'message': 'This Proxmox Admin operation requires an approval workflow before execution',
            'approvals': result.get('approvals') or [],
            'decision_id': result.get('decision_id'),
        })
    effective = result.get('effective_context') or context
    effective_parameters = (effective.get('request') or {}).get('parameters')
    return (copy.deepcopy(effective_parameters) if isinstance(effective_parameters, dict) else parameters), result


def _event_name(command, suffix):
    normalized = command.replace('lxc.', 'container.').replace('.snapshot.', '.snapshot_').replace('.', '_')
    return f'proxmox.{normalized}.{suffix}'


def queue_operation(
    db,
    request,
    actor,
    *,
    provider_id,
    command,
    object_type,
    object_id,
    node=None,
    parameters=None,
    confirmation_expected=None,
    resource=None,
):
    ensure_enabled(db)
    permission = COMMAND_PERMISSIONS.get(command)
    if permission is None:
        raise HTTPException(422, {'code': 'UNSUPPORTED_OPERATION', 'message': 'Unsupported Proxmox Admin command'})
    permissions = request_permissions(request)
    if permission not in permissions:
        raise HTTPException(403, {'code': 'PERMISSION_DENIED', 'message': f'Permission required: {permission}'})
    if command in GLOBAL_NATIVE_MUTATIONS and 'proxmox_admin.scope.all' not in permissions:
        raise HTTPException(403, {
            'code': 'GLOBAL_NATIVE_PERMISSION_REQUIRED',
            'message': 'This native Proxmox operation requires global Proxmox Admin scope',
        })

    provider, _, adapter = get_proxmox_provider(db, provider_id)
    parameters = dict(parameters or {})
    if confirmation_expected is not None:
        supplied = str(parameters.pop('confirmation', '') or '')
        if supplied != str(confirmation_expected):
            raise HTTPException(422, {
                'code': 'CONFIRMATION_MISMATCH',
                'message': f'Confirmation must exactly match {confirmation_expected}',
            })

    if object_type in {'vm', 'container'}:
        visible = _ensure_visible_object(
            db, provider_id, request, node, int(object_id),
            'vm' if object_type == 'vm' else 'lxc',
        )
        resource = {**dict(visible), **dict(resource or {})}
    else:
        resource = dict(resource or {})
    resource.update({
        'type': object_type,
        'id': str(object_id),
        'provider_id': int(provider_id),
        'provider_name': provider.name,
        'node': node,
    })

    before = _target_snapshot(adapter, object_type, node, object_id) if node else {}
    scope = getattr(request.state, 'resource_scope', DEFAULT_SCOPE)
    parameters, policy = evaluate_operation_policy(
        db, actor, permissions, scope, command, resource, parameters, phase='pre_proxmox_admin'
    )

    resource_key = _resource_key(provider_id, object_type, object_id)
    lock_resource_id = _lock_resource_id(resource_key)
    request_id = str(getattr(request.state, 'request_id', '') or uuid.uuid4())
    action_request = Day2ActionRequest(
        resource_id=lock_resource_id,
        deployment_id=None,
        action='pxadmin.' + command,
        parameters=redact(parameters),
        reason='Proxmox Admin operation',
        requested_by=actor.user_id,
        approval_state='not_required',
        status='QUEUED',
        request_id=request_id,
        safe_diff={'before': before, 'requested': redact(parameters)},
        result={'internal_lock_owner': True, 'resource_key': resource_key},
    )
    db.add(action_request)
    db.flush()

    payload = {
        '_proxmox_admin': {
            'provider_id': int(provider_id),
            'provider_name': provider.name,
            'cluster': provider.name,
            'node': node,
            'object_type': object_type,
            'object_id': str(object_id),
            'resource_key': resource_key,
            'lock_resource_id': lock_resource_id,
            'action_request_id': action_request.id,
            'command': command,
            'permission': permission,
            'parameters': redact(parameters),
            'resource': redact(resource),
            'before': before,
            'after': None,
            'upid': None,
            'task': None,
            'requested_at': now().isoformat() + 'Z',
            'started_at': None,
            'finished_at': None,
            'reconciliation_required': False,
            'policy': {
                'decision_id': policy.get('decision_id'),
                'matched_policy_ids': policy.get('matched_policy_ids') or [],
                'obligations': policy.get('obligations') or [],
            },
        }
    }
    job = Job(
        deployment_id=None,
        operation='pxadmin.exec',
        payload=payload,
        status='queued',
        created_by=actor.user_id,
        token_id=actor.id,
        request_id=request_id,
        ip=request.client.host if request.client else '',
        source=getattr(request.state, 'source', 'API'),
    )
    db.add(job)
    db.flush()
    action_request.job_id = job.id
    acquire_resource_lock(
        db,
        lock_resource_id,
        action_request.id,
        day2_settings(db)['resource_lock_timeout'],
    )
    db.add(JobLog(job_id=job.id, message=f'proxmox.admin.requested: {command} {resource_key}'))
    details = {
        'actor': actor.user_id,
        'operation': command,
        'resource': object_type,
        'resource_id': str(object_id),
        'provider': provider.name,
        'node': node,
        'organization': scope.tenant_id,
        'project': scope.project_id,
        'before': before,
        'requested_change': redact(parameters),
        'after': None,
        'job': job.id,
        'proxmox_upid': None,
        'result': 'queued',
    }
    audit(
        db, request, _event_name(command, 'requested'),
        'proxmox_resources', resource_key, details=details,
    )
    queue_webhook_event(db, _event_name(command, 'requested'), resource_key, {
        'job': {'id': job.id, 'request_id': request_id},
        'provider_id': provider_id,
        'node': node,
        'object_type': object_type,
        'object_id': str(object_id),
        'requested_change': redact(parameters),
    })
    return {
        'job_id': job.id,
        'status': job.status,
        'operation': command,
        'resource_key': resource_key,
    }


def public_job(job):
    meta = dict((job.payload or {}).get('_proxmox_admin') or {})
    return {
        'id': job.id,
        'operation': meta.get('command') or job.operation,
        'provider_id': meta.get('provider_id'),
        'cluster': meta.get('cluster'),
        'node': meta.get('node'),
        'object_type': meta.get('object_type'),
        'object_id': meta.get('object_id'),
        'upid': meta.get('upid'),
        'status': job.status,
        'task': meta.get('task'),
        'progress': (meta.get('task') or {}).get('progress') if isinstance(meta.get('task'), dict) else None,
        'created_at': job.created_at.isoformat() + 'Z' if job.created_at else None,
        'started_at': meta.get('started_at'),
        'finished_at': meta.get('finished_at'),
        'error': job.error,
        'retry_of': job.retry_of,
        'attempt': job.attempt,
        'reconciliation_required': bool(meta.get('reconciliation_required')),
        'before': meta.get('before'),
        'after': meta.get('after'),
    }


def get_admin_job(db, job_id):
    job = db.get(Job, job_id)
    if job is None or not job.operation.startswith('pxadmin.'):
        raise HTTPException(404, {'code': 'JOB_NOT_FOUND', 'message': 'Proxmox Admin job not found'})
    return public_job(job)


def list_admin_jobs(db, *, limit=100):
    rows = db.scalars(
        select(Job).where(Job.operation.like('pxadmin.%')).order_by(Job.created_at.desc()).limit(limit)
    ).all()
    return [public_job(row) for row in rows]


def cancel_admin_job(db, request, job):
    if job.status not in {'queued', 'running', 'cancelling'}:
        raise HTTPException(409, {'code': 'INVALID_STATE', 'message': 'Job cannot be cancelled in its current state'})
    job.cancel_requested = True
    if job.status == 'running':
        job.status = 'cancelling'
    db.add(JobLog(job_id=job.id, message='proxmox.admin.cancel_requested'))
    audit(
        db, request, 'proxmox.job.cancel_requested', 'jobs', job.id,
        details={'job': job.id, 'result': job.status},
    )
    return public_job(job)


def reconcile_required_job(db, request, actor, job):
    ensure_enabled(db)
    if job is None or not job.operation.startswith('pxadmin.'):
        raise HTTPException(404, {'code': 'JOB_NOT_FOUND', 'message': 'Proxmox Admin job not found'})
    if job.status != 'reconciliation_required':
        raise HTTPException(409, {
            'code': 'RECONCILIATION_NOT_REQUIRED',
            'message': 'This job is not waiting for reconciliation',
        })
    meta = dict((job.payload or {}).get('_proxmox_admin') or {})
    permission = str(meta.get('permission') or '')
    if permission not in request_permissions(request):
        raise HTTPException(403, {
            'code': 'PERMISSION_DENIED',
            'message': f'Permission required: {permission}',
        })
    _, _, adapter = get_proxmox_provider(db, meta['provider_id'])
    upid = meta.get('upid')
    if upid:
        task = adapter.task_status(meta.get('node'), upid) or {}
        if str(task.get('status') or '').lower() != 'stopped':
            raise HTTPException(409, {
                'code': 'PROXMOX_TASK_STILL_RUNNING',
                'message': 'The Proxmox task is still running; the resource cannot be reconciled yet',
            })
        meta['task'] = {
            'status': task.get('status'),
            'exitstatus': task.get('exitstatus'),
            'starttime': task.get('starttime'),
            'endtime': task.get('endtime'),
            **({'progress': task.get('progress')} if task.get('progress') is not None else {}),
        }
    ok, after = reconcile(adapter, meta, dict(meta.get('parameters') or {}))
    meta['after'] = redact(after)
    meta['reconciliation_required'] = False
    meta['finished_at'] = now().isoformat() + 'Z'
    payload = dict(job.payload or {})
    payload['_proxmox_admin'] = meta
    job.payload = payload
    job.status = 'successful' if ok else 'failed'
    job.error = None if ok else (
        'RECONCILIATION_MISMATCH: live Proxmox state does not match the requested post-condition'
    )
    request_row = db.get(Day2ActionRequest, meta.get('action_request_id'))
    if request_row is not None:
        request_row.status = 'SUCCEEDED' if ok else 'FAILED'
        request_row.finished_at = now()
        request_row.error_code = None if ok else 'RECONCILIATION_MISMATCH'
        request_row.error_message = job.error
        request_row.result = {
            **(request_row.result or {}),
            'reconciliation_required': False,
            'after': meta['after'],
        }
        release_resource_lock(db, meta['lock_resource_id'], request_row.id)

    details = {
        'actor': actor.user_id,
        'operation': meta.get('command'),
        'resource': meta.get('object_type'),
        'resource_id': meta.get('object_id'),
        'provider': meta.get('provider_name'),
        'node': meta.get('node'),
        'organization': job.tenant_id,
        'project': job.project_id,
        'before': meta.get('before'),
        'requested_change': meta.get('parameters'),
        'after': meta.get('after'),
        'job': job.id,
        'proxmox_upid': meta.get('upid'),
        'result': job.status,
    }
    audit(
        db, request, 'proxmox.resource.reconciled', 'proxmox_resources', meta.get('resource_key'),
        result='success' if ok else 'failed', details=details,
    )
    queue_webhook_event(db, 'proxmox.resource.reconciled', meta.get('resource_key'), {
        'job': {'id': job.id, 'request_id': job.request_id},
        'provider_id': meta.get('provider_id'),
        'node': meta.get('node'),
        'object_type': meta.get('object_type'),
        'object_id': meta.get('object_id'),
        'state': meta.get('after'),
        'matches_requested_state': ok,
    })
    queue_webhook_event(
        db,
        _event_name(meta.get('command'), 'completed' if ok else 'failed'),
        meta.get('resource_key'),
        {
            'job': {'id': job.id, 'request_id': job.request_id},
            'upid': meta.get('upid'),
            'result': job.status,
        },
    )
    db.add(JobLog(job_id=job.id, message='proxmox.admin.manual_reconciliation: ' + job.status))
    queue_job_webhooks(db, job)
    return public_job(job)


def bulk_summary(db, job_ids):
    rows = db.scalars(select(Job).where(Job.id.in_(job_ids))).all() if job_ids else []
    counts = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    completed = sum(value for key, value in counts.items() if key in TERMINAL_JOB_STATUSES)
    return {
        'total': len(job_ids),
        'completed': completed,
        'running': counts.get('running', 0) + counts.get('cancelling', 0),
        'failed': counts.get('failed', 0) + counts.get('reconciliation_required', 0),
        'queued': counts.get('queued', 0),
        'statuses': counts,
        'jobs': [public_job(row) for row in rows],
    }
