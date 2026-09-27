from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from app.database import session
from app.day2.models import Day2ActionRequest
from app.day2.service import (
    day2_settings,
    refresh_resource_lock,
    release_resource_lock,
)
from app.instance_operation import normal_instance_operation
from app.models import Audit, Job, JobLog, Token, now
from app.operations.service import queue_job_webhooks, queue_webhook_event
from app.proxmox_admin.service import (
    COMMAND_PERMISSIONS,
    _ensure_visible_object,
    _event_name,
    _target_snapshot,
    ensure_enabled,
    evaluate_operation_policy,
    get_proxmox_provider,
)
from app.resource_scope.authorization import Scope, authorize
from app.resource_scope.database import bind_scope
from app.resource_scope.permissions import RESOURCE_PERMISSIONS
from app.security.core import _safe_audit_details
from app.tenancy.authorization import Principal


class ProxmoxAdminFailure(RuntimeError):
    def __init__(self, code, message, *, reconciliation_required=False):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.reconciliation_required = bool(reconciliation_required)


class ProxmoxAdminCancelled(RuntimeError):
    pass


def _meta(job):
    value = dict((job.payload or {}).get('_proxmox_admin') or {})
    if not value:
        raise ProxmoxAdminFailure('INVALID_JOB', 'Missing Proxmox Admin job metadata')
    return value


def _save_meta(job, meta):
    payload = dict(job.payload or {})
    payload['_proxmox_admin'] = dict(meta)
    job.payload = payload


def _reauthorize(db, job, *, write=True):
    meta = _meta(job)
    permission = str(meta.get('permission') or '')
    if permission not in COMMAND_PERMISSIONS.values():
        raise ProxmoxAdminFailure('PERMISSION_DENIED', 'Invalid persisted Proxmox Admin permission')
    if job.token_id is None:
        raise ProxmoxAdminFailure('PERMISSION_DENIED', 'Proxmox Admin job authorization is missing')

    scope = Scope(str(job.tenant_id), str(job.project_id))
    try:
        identity, scoped = authorize(
            db,
            Principal(int(job.created_by), int(job.token_id)),
            scope,
            permission,
            write=write,
        )
        bind_scope(db, scope)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        raise ProxmoxAdminFailure(
            str(detail.get('code') or 'PERMISSION_DENIED'),
            str(detail.get('message') or 'Proxmox Admin authorization is no longer valid'),
        ) from None

    permissions = (set(identity.global_permissions) - set(RESOURCE_PERMISSIONS)) | set(scoped)
    if meta.get('command') in {
        'node.service', 'backup.restore', 'backup.delete',
        'firewall.rule.create', 'firewall.rule.update', 'firewall.rule.delete',
    } and 'proxmox_admin.scope.all' not in permissions:
        raise ProxmoxAdminFailure(
            'GLOBAL_NATIVE_PERMISSION_REQUIRED',
            'Global Proxmox Admin scope was revoked before execution',
        )
    ensure_enabled(db)
    return identity, permissions, scope


class Context:
    def __init__(self, job_id, timeout=7200):
        self.job_id = job_id
        self.started = time.monotonic()
        self.timeout = timeout
        self.last_check = 0.0
        self.upid = None
        self.node = None

    def check(self):
        if time.monotonic() - self.started > self.timeout:
            raise ProxmoxAdminFailure(
                'TIMEOUT',
                'Proxmox Admin operation exceeded the execution timeout',
                reconciliation_required=bool(self.upid),
            )
        if time.monotonic() - self.last_check < 1:
            return
        self.last_check = time.monotonic()
        with session() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                raise ProxmoxAdminFailure('JOB_NOT_FOUND', 'Proxmox Admin job no longer exists')
            meta = _meta(job)
            request_row = db.get(Day2ActionRequest, meta.get('action_request_id'))
            if request_row is None:
                raise ProxmoxAdminFailure('LOCK_OWNER_MISSING', 'Proxmox Admin resource lock owner is missing')
            _reauthorize(db, job, write=True)
            if job.cancel_requested:
                raise ProxmoxAdminCancelled('Cancellation requested')
            job.heartbeat_at = now()
            refresh_resource_lock(
                db,
                meta['lock_resource_id'],
                request_row.id,
                day2_settings(db)['resource_lock_timeout'],
            )
            db.commit()

    def log(self, message):
        with session() as db:
            db.add(JobLog(job_id=self.job_id, message=str(message)[:8192]))
            db.commit()

    def set_task(self, node, upid, task=None):
        self.node = node
        self.upid = upid
        with session() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                return
            meta = _meta(job)
            meta['upid'] = upid
            if task is not None:
                meta['task'] = task
            _save_meta(job, meta)
            job.heartbeat_at = now()
            db.commit()

    def task_state(self, task):
        with session() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                return
            meta = _meta(job)
            meta['task'] = task
            _save_meta(job, meta)
            job.heartbeat_at = now()
            db.commit()


def _wait_task(ctx, adapter, node, upid):
    if not isinstance(upid, str) or not upid.startswith('UPID:'):
        return None
    ctx.set_task(node, upid, {'status': 'accepted'})
    while True:
        ctx.check()
        try:
            status = adapter.task_status(node, upid) or {}
        except HTTPException as error:
            raise ProxmoxAdminFailure(
                'TASK_STATUS_UNAVAILABLE',
                'Cannot determine the current Proxmox task state',
                reconciliation_required=True,
            ) from error
        task = {
            'status': status.get('status'),
            'exitstatus': status.get('exitstatus'),
            'starttime': status.get('starttime'),
            'endtime': status.get('endtime'),
        }
        if status.get('progress') is not None:
            task['progress'] = status.get('progress')
        ctx.task_state(task)
        if str(status.get('status') or '').lower() == 'stopped':
            try:
                logs = adapter.task_log(node, upid, start=0, limit=500)
                for row in logs[-50:]:
                    message = row.get('t') if isinstance(row, dict) else str(row)
                    if message:
                        ctx.log('proxmox.task: ' + str(message))
            except HTTPException:
                pass
            if str(status.get('exitstatus') or '') != 'OK':
                raise ProxmoxAdminFailure(
                    'PROXMOX_TASK_FAILED',
                    'Proxmox task failed: ' + str(status.get('exitstatus') or 'unknown error'),
                )
            return task
        time.sleep(1)


def _wait_result(ctx, adapter, node, result):
    if isinstance(result, (list, tuple)):
        final = None
        for item in result:
            final = _wait_task(ctx, adapter, node, item)
        return final
    return _wait_task(ctx, adapter, node, result)


def _vm_config_values(params):
    mapping = {
        'cores': 'cores',
        'sockets': 'sockets',
        'memory_mb': 'memory',
        'balloon_mb': 'balloon',
        'tags': 'tags',
        'description': 'description',
        'boot': 'boot',
        'onboot': 'onboot',
        'protection': 'protection',
        'agent': 'agent',
    }
    values = {}
    for key, target in mapping.items():
        if key not in params or params[key] is None:
            continue
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        values[target] = value
    return values


def _lxc_config_values(params):
    mapping = {
        'cores': 'cores',
        'memory_mb': 'memory',
        'swap_mb': 'swap',
        'tags': 'tags',
        'description': 'description',
        'onboot': 'onboot',
        'protection': 'protection',
    }
    values = {}
    for key, target in mapping.items():
        if key not in params or params[key] is None:
            continue
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        values[target] = value
    return values


def _nic_value(adapter, node, vmid, params):
    current = adapter.vm_config(node, vmid) or {}
    existing = str(current.get(params['nic']) or '')
    mac = params.get('mac')
    if not mac and '=' in existing:
        mac = existing.split('=', 1)[1].split(',', 1)[0]
    model = params.get('model') or 'virtio'
    first = model + (('=' + mac) if mac else '')
    parts = [first, 'bridge=' + params['bridge']]
    if params.get('vlan') is not None:
        parts.append('tag=' + str(int(params['vlan'])))
    if params.get('firewall'):
        parts.append('firewall=1')
    if params.get('rate_mbps') is not None:
        parts.append('rate=' + str(params['rate_mbps']))
    return ','.join(parts)


def _invoke(adapter, meta, params):
    command = meta['command']
    node = meta.get('node')
    object_id = int(meta['object_id']) if str(meta.get('object_id', '')).isdigit() else meta.get('object_id')

    if command == 'vm.power':
        return adapter.vm_power(node, object_id, params['action'])
    if command == 'vm.config':
        return adapter.update_vm_config(node, object_id, **_vm_config_values(params))
    if command == 'vm.cloudinit':
        return adapter.update_vm_config(node, object_id, **{k: v for k, v in params.items() if v is not None})
    if command == 'vm.clone':
        return adapter.clone_vm(
            node, object_id, new_vm_id=params['new_vmid'], name=params['name'],
            target=params.get('target'), full=params.get('full', True),
            storage=params.get('storage'), pool=params.get('pool'),
        )
    if command == 'vm.migrate':
        return adapter.migrate_vm(
            node, object_id, target=params['target'], online=params.get('online', False),
            with_local_disks=params.get('with_local_disks', False),
        )
    if command == 'vm.template':
        return adapter.convert_to_template(node, object_id)
    if command == 'vm.delete':
        return adapter.delete_vm(
            node, object_id, purge=params.get('purge', False),
            destroy_unreferenced_disks=params.get('destroy_unreferenced_disks', False),
        )
    if command == 'vm.disk.resize':
        return adapter.resize_disk(node, object_id, disk=params['disk'], grow_gib=params['grow_gib'])
    if command == 'vm.disk.add':
        parts = [f"{params['storage']}:{int(params['size_gib'])}"]
        if params.get('discard'):
            parts.append('discard=on')
        if params.get('ssd'):
            parts.append('ssd=1')
        return adapter.update_vm_config(node, object_id, **{params['disk']: ','.join(parts)})
    if command == 'vm.disk.remove':
        config = adapter.vm_config(node, object_id) or {}
        volume = str(config.get(params['disk']) or '').split(',', 1)[0]
        tasks = [adapter.delete_vm_config_key(node, object_id, params['disk'])]
        if params.get('delete_volume') and ':' in volume:
            storage = volume.split(':', 1)[0]
            tasks.append(adapter.delete_storage_volume(node, storage, volume))
        return tasks
    if command == 'vm.disk.move':
        return adapter.move_disk(
            node, object_id, disk=params['disk'], storage=params['storage'],
            delete_source=params.get('delete_source', True),
        )
    if command == 'vm.nic.set':
        return adapter.update_vm_config(
            node, object_id, **{params['nic']: _nic_value(adapter, node, object_id, params)}
        )
    if command == 'vm.nic.remove':
        return adapter.delete_vm_config_key(node, object_id, params['nic'])
    if command == 'vm.snapshot.create':
        return adapter.create_snapshot(
            node, object_id, params['name'], params.get('description', ''), params.get('include_ram', False)
        )
    if command == 'vm.snapshot.delete':
        return adapter.delete_snapshot(node, object_id, params['name'])
    if command == 'vm.snapshot.rollback':
        return adapter.rollback_snapshot(node, object_id, params['name'])

    if command == 'lxc.power':
        return adapter.lxc_power(node, object_id, params['action'])
    if command == 'lxc.config':
        return adapter.update_lxc_config(node, object_id, **_lxc_config_values(params))
    if command == 'lxc.clone':
        return adapter.clone_lxc(
            node, object_id, new_vm_id=params['new_vmid'], hostname=params['name'],
            target=params.get('target'), full=params.get('full', True),
            storage=params.get('storage'), pool=params.get('pool'),
        )
    if command == 'lxc.migrate':
        return adapter.migrate_lxc(
            node, object_id, target=params['target'], restart=params.get('online', False),
            with_local_disks=params.get('with_local_disks', False),
        )
    if command == 'lxc.delete':
        return adapter.delete_lxc(
            node, object_id, purge=params.get('purge', False),
            destroy_unreferenced_disks=params.get('destroy_unreferenced_disks', False),
        )
    if command == 'lxc.snapshot.create':
        return adapter.create_lxc_snapshot(node, object_id, params['name'], params.get('description', ''))
    if command == 'lxc.snapshot.delete':
        return adapter.delete_lxc_snapshot(node, object_id, params['name'])
    if command == 'lxc.snapshot.rollback':
        return adapter.rollback_lxc_snapshot(node, object_id, params['name'])

    if command == 'backup.run':
        return adapter.backup_vm(
            node, object_id, storage=params['storage'], mode=params.get('mode', 'snapshot'),
            compress=params.get('compress', 'zstd'), notes=params.get('notes'),
        )
    if command == 'backup.restore':
        return adapter.restore_vm(
            params['node'], vm_id=params['vmid'], archive=params['archive'],
            storage=params.get('storage'), unique=params.get('unique', True),
        )
    if command == 'backup.delete':
        return adapter.delete_storage_volume(params['node'], params['storage'], params['volume'])
    if command == 'node.service':
        return adapter.node_service_action(node, str(object_id), params['action'])

    if command.startswith('firewall.rule.'):
        level = params.pop('_level')
        firewall_node = params.pop('_node', None)
        firewall_object_id = params.pop('_object_id', None)
        if command == 'firewall.rule.create':
            return adapter.create_firewall_rule(level, params, node=firewall_node, object_id=firewall_object_id)
        pos = int(params.pop('pos'))
        if command == 'firewall.rule.update':
            return adapter.update_firewall_rule(level, pos, params, node=firewall_node, object_id=firewall_object_id)
        return adapter.delete_firewall_rule(level, pos, node=firewall_node, object_id=firewall_object_id)

    raise ProxmoxAdminFailure('UNSUPPORTED_OPERATION', 'Persisted Proxmox Admin command is not supported')


def _find_native(adapter, object_type, object_id):
    wanted = 'qemu' if object_type == 'vm' else 'lxc'
    for row in adapter.cluster_resources('vm'):
        if row.get('type') == wanted and int(row.get('vmid', -1)) == int(object_id):
            return dict(row)
    return None


def _config_contains(actual, expected):
    for key, value in expected.items():
        if value is None:
            continue
        actual_value = actual.get(key)
        if isinstance(value, bool):
            if int(actual_value or 0) != int(value):
                return False
        elif str(actual_value) != str(value):
            return False
    return True


def _reconcile(adapter, meta, params):
    command = meta['command']
    node = meta.get('node')
    object_id = meta.get('object_id')

    if meta['object_type'] in {'vm', 'container'}:
        native = _find_native(adapter, meta['object_type'], object_id)
        if command in {'vm.delete', 'lxc.delete'}:
            return native is None, {'resource': native}
        if native is None:
            return False, {'resource': None}

        current_node = native.get('node') or node
        if command in {'vm.migrate', 'lxc.migrate'}:
            return str(current_node) == str(params['target']), {'resource': native}

        if command == 'vm.clone' or command == 'lxc.clone':
            clone_kind = meta['object_type']
            clone = _find_native(adapter, clone_kind, params['new_vmid'])
            return clone is not None, {'resource': native, 'clone': clone}

        if command == 'vm.template':
            return bool(native.get('template')), {'resource': native}

        if command.endswith('snapshot.create') or command.endswith('snapshot.delete'):
            snapshots = (
                adapter.snapshots(current_node, int(object_id))
                if meta['object_type'] == 'vm'
                else adapter.lxc_snapshots(current_node, int(object_id))
            )
            names = {str(row.get('name')) for row in snapshots}
            expected = params['name'] in names if command.endswith('create') else params['name'] not in names
            return expected, {'resource': native, 'snapshots': snapshots}

        if command.endswith('snapshot.rollback'):
            state = (
                adapter.vm_status(current_node, int(object_id))
                if meta['object_type'] == 'vm'
                else adapter.lxc_status(current_node, int(object_id))
            )
            return bool(state), {'resource': native, 'status': state}

        if command == 'vm.power':
            status = adapter.vm_status(current_node, int(object_id)) or {}
            expected = {
                'start': {'running'},
                'shutdown': {'stopped'},
                'stop': {'stopped'},
                'reboot': {'running'},
                'reset': {'running'},
                'suspend': {'paused', 'suspended'},
                'resume': {'running'},
            }[params['action']]
            return str(status.get('status') or '').lower() in expected, {'resource': native, 'status': status}

        if command == 'lxc.power':
            status = adapter.lxc_status(current_node, int(object_id)) or {}
            expected = {
                'start': {'running'}, 'shutdown': {'stopped'}, 'stop': {'stopped'},
                'reboot': {'running'}, 'suspend': {'paused', 'suspended'}, 'resume': {'running'},
            }[params['action']]
            return str(status.get('status') or '').lower() in expected, {'resource': native, 'status': status}

        if command == 'vm.config':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            expected = _vm_config_values(params)
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command == 'vm.cloudinit':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            expected = {key: value for key, value in params.items() if value is not None}
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command == 'lxc.config':
            config = adapter.lxc_config(current_node, int(object_id)) or {}
            expected = _lxc_config_values(params)
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command == 'vm.disk.resize':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['disk'] in config, {'resource': native, 'config': config}

        if command == 'vm.disk.add':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['disk'] in config, {'resource': native, 'config': config}

        if command == 'vm.disk.remove':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['disk'] not in config, {'resource': native, 'config': config}

        if command == 'vm.disk.move':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return str(config.get(params['disk']) or '').startswith(params['storage'] + ':'), {'resource': native, 'config': config}

        if command == 'vm.nic.set':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            value = str(config.get(params['nic']) or '')
            return params['bridge'] in value, {'resource': native, 'config': config}

        if command == 'vm.nic.remove':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['nic'] not in config, {'resource': native, 'config': config}

        if command == 'backup.run':
            backups = adapter.backups(current_node, params['storage'], int(object_id))
            return bool(backups), {'resource': native, 'backups': backups}

        return True, {'resource': native}

    if command == 'backup.restore':
        restored = _find_native(adapter, 'vm', params['vmid'])
        return restored is not None, {'resource': restored}

    if command == 'backup.delete':
        rows = adapter.storage_content(params['node'], params['storage'], content='backup')
        exists = any(str(row.get('volid')) == str(params['volume']) for row in rows)
        return not exists, {'backups': rows}

    if command == 'node.service':
        services = adapter.node_services(node)
        service = next((row for row in services if str(row.get('service')) == str(object_id)), None)
        action = params['action']
        if action == 'stop':
            ok = service is not None and str(service.get('state') or '').lower() not in {'running', 'active'}
        else:
            ok = service is not None and str(service.get('state') or '').lower() in {'running', 'active'}
        return ok, {'service': service}

    if command.startswith('firewall.rule.'):
        level = params.get('_level')
        snapshot = adapter.firewall_snapshot(level, node=params.get('_node'), object_id=params.get('_object_id'))
        return True, {'firewall': snapshot}

    return False, {}


def _audit_worker(db, job, meta, *, action, result):
    details = {
        'actor': job.created_by,
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
        'result': result,
    }
    db.add(Audit(
        user_id=job.created_by,
        token_id=job.token_id,
        ip=job.ip,
        source=job.source,
        action=action,
        resource='proxmox_resources',
        resource_id=meta.get('resource_key'),
        result=result,
        request_id=job.request_id,
        details=_safe_audit_details(details),
    ))


def _finish(job_id, *, status, error=None, after=None, reconciliation_required=False, event_suffix=None):
    with session() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        meta = _meta(job)
        request_row = db.get(Day2ActionRequest, meta.get('action_request_id'))
        meta['after'] = _safe_audit_details(after or {})
        meta['finished_at'] = now().isoformat() + 'Z'
        meta['reconciliation_required'] = bool(reconciliation_required)
        _save_meta(job, meta)
        job.status = status
        job.error = str(error)[:8192] if error else None
        if request_row is not None:
            request_row.finished_at = now()
            request_row.status = (
                'SUCCEEDED' if status == 'successful'
                else 'CANCELLED' if status == 'cancelled'
                else 'FAILED'
            )
            request_row.error_code = None if status == 'successful' else (
                'RECONCILIATION_REQUIRED' if reconciliation_required else 'PROXMOX_ADMIN_FAILED'
            )
            request_row.error_message = job.error
            request_row.result = {
                **(request_row.result or {}),
                'reconciliation_required': bool(reconciliation_required),
                'after': meta['after'],
            }
            if not reconciliation_required:
                release_resource_lock(db, meta['lock_resource_id'], request_row.id)
        suffix = event_suffix or ('completed' if status == 'successful' else 'failed')
        queue_webhook_event(db, _event_name(meta['command'], suffix), meta['resource_key'], {
            'job': {'id': job.id, 'request_id': job.request_id},
            'provider_id': meta.get('provider_id'),
            'node': meta.get('node'),
            'object_type': meta.get('object_type'),
            'object_id': meta.get('object_id'),
            'upid': meta.get('upid'),
            'result': status,
            'reconciliation_required': bool(reconciliation_required),
        })
        if after is not None:
            queue_webhook_event(db, 'proxmox.resource.reconciled', meta['resource_key'], {
                'job': {'id': job.id, 'request_id': job.request_id},
                'provider_id': meta.get('provider_id'),
                'object_type': meta.get('object_type'),
                'object_id': meta.get('object_id'),
                'state': meta['after'],
            })
        _audit_worker(
            db, job, meta,
            action=_event_name(meta['command'], suffix),
            result='success' if status == 'successful' else status,
        )
        db.add(JobLog(job_id=job.id, message=f'proxmox.admin.{suffix}: {status}'))
        queue_job_webhooks(db, job)
        db.commit()


def _execute_unfenced(job_id):
    ctx = Context(job_id)
    try:
        with session() as db:
            job = db.get(Job, job_id)
            if job is None or not job.operation.startswith('pxadmin.'):
                return
            if job.status not in {'queued', 'running'}:
                return
            identity, permissions, scope = _reauthorize(db, job, write=True)
            meta = _meta(job)
            request_row = db.get(Day2ActionRequest, meta.get('action_request_id'))
            if request_row is None or request_row.job_id != job.id:
                raise ProxmoxAdminFailure('LOCK_OWNER_MISSING', 'Resource lock owner does not match the job')

            provider, _, adapter = get_proxmox_provider(db, meta['provider_id'])
            resource = dict(meta.get('resource') or {})
            pseudo_request = SimpleNamespace(
                state=SimpleNamespace(permissions=permissions, resource_scope=scope)
            )
            if meta['object_type'] in {'vm', 'container'}:
                _ensure_visible_object(
                    db, meta['provider_id'], pseudo_request, meta['node'], int(meta['object_id']),
                    'vm' if meta['object_type'] == 'vm' else 'lxc',
                )
            persisted_params = dict(meta.get('parameters') or {})
            effective_params, policy = evaluate_operation_policy(
                db, identity, permissions, scope, meta['command'], resource, persisted_params,
                phase='worker_revalidate',
            )
            if effective_params != persisted_params:
                raise ProxmoxAdminFailure(
                    'POLICY_INPUT_DRIFT',
                    'Policy Engine would change the queued parameters; submit the operation again',
                )
            job.status = 'running'
            job.heartbeat_at = now()
            meta['started_at'] = now().isoformat() + 'Z'
            meta['policy']['worker_decision_id'] = policy.get('decision_id')
            _save_meta(job, meta)
            request_row.status = 'RUNNING'
            request_row.started_at = now()
            db.add(JobLog(job_id=job.id, message='proxmox.admin.running: ' + meta['command']))
            _audit_worker(db, job, meta, action=_event_name(meta['command'], 'started'), result='running')
            queue_webhook_event(db, _event_name(meta['command'], 'started'), meta['resource_key'], {
                'job': {'id': job.id, 'request_id': job.request_id},
                'provider_id': meta['provider_id'],
                'node': meta.get('node'),
                'object_type': meta['object_type'],
                'object_id': meta['object_id'],
            })
            db.commit()

        ctx.check()
        result = _invoke(adapter, meta, dict(meta.get('parameters') or {}))
        _wait_result(ctx, adapter, meta.get('node') or dict(meta.get('parameters') or {}).get('node'), result)
        ctx.check()
        ok, after = _reconcile(adapter, meta, dict(meta.get('parameters') or {}))
        if not ok:
            raise ProxmoxAdminFailure(
                'RECONCILIATION_MISMATCH',
                'Proxmox task completed but the live resource state does not match the requested state',
                reconciliation_required=True,
            )
        _finish(job_id, status='successful', after=after, reconciliation_required=False)
    except ProxmoxAdminCancelled:
        try:
            with session() as db:
                job = db.get(Job, job_id)
                meta = _meta(job) if job else {}
                _, _, adapter = get_proxmox_provider(db, meta['provider_id']) if job else (None, None, None)
            if adapter is not None and ctx.upid and ctx.node:
                try:
                    adapter.stop_task(ctx.node, ctx.upid)
                except HTTPException:
                    pass
                try:
                    _wait_task(ctx, adapter, ctx.node, ctx.upid)
                except (ProxmoxAdminFailure, ProxmoxAdminCancelled):
                    pass
                try:
                    _, after = _reconcile(adapter, meta, dict(meta.get('parameters') or {}))
                except Exception:
                    after = None
            else:
                after = {}
            _finish(
                job_id,
                status='cancelled',
                error='Cancellation requested',
                after=after,
                reconciliation_required=after is None,
                event_suffix='cancelled',
            )
        except Exception:
            _finish(
                job_id,
                status='reconciliation_required',
                error='Cancellation interrupted an in-flight Proxmox operation; reconciliation is required',
                after=None,
                reconciliation_required=True,
                event_suffix='failed',
            )
    except ProxmoxAdminFailure as error:
        after = None
        uncertain = error.reconciliation_required
        try:
            with session() as db:
                job = db.get(Job, job_id)
                meta = _meta(job) if job else {}
                _, _, adapter = get_proxmox_provider(db, meta['provider_id']) if job else (None, None, None)
            if adapter is not None:
                _, after = _reconcile(adapter, meta, dict(meta.get('parameters') or {}))
                if after is not None and not error.reconciliation_required:
                    uncertain = False
        except Exception:
            if ctx.upid:
                uncertain = True
        _finish(
            job_id,
            status='reconciliation_required' if uncertain else 'failed',
            error=f'{error.code}: {error.message}',
            after=after,
            reconciliation_required=uncertain,
            event_suffix='failed',
        )
    except HTTPException as error:
        detail = error.detail
        if isinstance(detail, dict):
            code = detail.get('code') or 'PROXMOX_API_ERROR'
            message = detail.get('message') or str(detail)
        else:
            code, message = 'PROXMOX_API_ERROR', str(detail)
        _finish(
            job_id,
            status='reconciliation_required' if ctx.upid else 'failed',
            error=f'{code}: {message}',
            after=None,
            reconciliation_required=bool(ctx.upid),
            event_suffix='failed',
        )
    except Exception as error:
        _finish(
            job_id,
            status='reconciliation_required' if ctx.upid else 'failed',
            error='PROXMOX_ADMIN_EXECUTION_FAILED: ' + str(error)[:1000],
            after=None,
            reconciliation_required=bool(ctx.upid),
            event_suffix='failed',
        )


def execute(job_id):
    with normal_instance_operation():
        return _execute_unfenced(job_id)
