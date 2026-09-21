import math
import re
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app.day2.errors import failure
from app.models import Credential
from app.providers.proxmox import ProxmoxProvider
from app.providers.registry import provider_for


_DISK_KEY = re.compile(r'^(?:scsi|virtio|sata|ide)\\d{1,2}$')
_NIC_KEY = re.compile(r'^net\\d{1,2}$')
_UNUSED_KEY = re.compile(r'^unused\\d{1,2}$')
_SIZE = re.compile(r'(?:^|,)size=([0-9]+(?:\\.[0-9]+)?)([KMGT])(?:,|$)', re.I)


@dataclass(slots=True)
class Day2Target:
    resource_id: str
    provider_id: int
    provider_type: str
    resource_type: str
    name: str
    deployment_id: str | None
    management_mode: str
    node: str | None = None
    vm_id: int | None = None
    external_id: str | None = None
    primary_ip: str | None = None


def _parse_options(value):
    text = str(value or '')
    parts = [item for item in text.split(',') if item]
    result = {'raw': text}
    if parts and '=' not in parts[0]:
        result['volume'] = parts[0]
    for part in parts:
        if '=' in part:
            key, item = part.split('=', 1)
            result[key] = item
        elif part:
            result.setdefault('model', part)
    return result


def _size_gib(spec):
    match = _SIZE.search(str(spec or ''))
    if not match:
        return None
    value = float(match.group(1))
    factors = {'K': 1 / 1024 / 1024, 'M': 1 / 1024, 'G': 1, 'T': 1024}
    return value * factors[match.group(2).upper()]


def _public_config(config):
    allowed = {'name', 'cores', 'sockets', 'memory', 'tags', 'onboot', 'agent', 'hotplug', 'ostype', 'boot'}
    result = {key: config.get(key) for key in allowed if key in config}
    for key, value in config.items():
        if _DISK_KEY.fullmatch(key) or _UNUSED_KEY.fullmatch(key) or _NIC_KEY.fullmatch(key):
            result[key] = value
    return result


class ProxmoxDay2Adapter:
    def __init__(self, credential: Credential):
        provider = provider_for(credential)
        if not isinstance(provider, ProxmoxProvider):
            raise failure('ACTION_NOT_SUPPORTED', message='Proxmox Day-2 adapter requires a Proxmox provider')
        self.provider = provider

    def _identity(self, target: Day2Target):
        if not target.node or target.vm_id is None:
            raise failure('RESOURCE_NOT_FOUND', message='Managed VM has incomplete Proxmox identity', status_code=404)
        return target.node, int(target.vm_id)

    def capabilities(self, target: Day2Target):
        node, vm_id = self._identity(target)
        config = self.provider.vm_config(node, vm_id) or {}
        hotplug = {item.strip() for item in str(config.get('hotplug') or '').split(',') if item.strip()}
        return {
            'provider': 'proxmox',
            'actions': [
                'power_on', 'power_off', 'shutdown', 'reboot', 'reset', 'suspend', 'resume',
                'create_snapshot', 'delete_snapshot', 'restore_snapshot',
                'resize_compute', 'add_disk', 'resize_disk', 'detach_disk', 'delete_disk',
                'add_nic', 'edit_nic', 'detach_nic', 'update_cloud_init',
                'update_tags', 'add_tag', 'remove_tag', 'migrate_vm', 'move_storage',
                'clone_vm', 'delete_vm', 'refresh_state',
            ],
            'power': True,
            'snapshots': True,
            'snapshot_memory': True,
            'snapshot_quiesce': False,
            'resize_cpu': True,
            'resize_memory': True,
            'hot_cpu_supported': 'cpu' in hotplug,
            'hot_memory_supported': 'memory' in hotplug,
            'disk_add': True,
            'disk_resize': True,
            'disk_shrink': False,
            'disk_detach': True,
            'disk_delete': True,
            'network_manage': True,
            'cloud_init': True,
            'live_migration': True,
            'storage_migration': True,
            'clone': True,
            'console': True,
        }

    def status(self, target: Day2Target):
        node, vm_id = self._identity(target)
        return self.provider.vm_status(node, vm_id) or {}

    def configuration(self, target: Day2Target):
        node, vm_id = self._identity(target)
        return self.provider.vm_config(node, vm_id) or {}

    def guest_addresses(self, target: Day2Target):
        node, vm_id = self._identity(target)
        try:
            return self.provider.guest_addresses(node, vm_id)
        except HTTPException:
            return [target.primary_ip] if target.primary_ip else []

    def snapshots(self, target: Day2Target):
        node, vm_id = self._identity(target)
        rows = self.provider.snapshots(node, vm_id) or []
        return [
            {
                'name': row.get('name'),
                'description': row.get('description') or '',
                'created': row.get('snaptime'),
                'state': 'current' if row.get('name') == 'current' else 'ready',
                'include_memory': bool(row.get('vmstate')),
                'provider_id': row.get('name'),
                'parent': row.get('parent'),
            }
            for row in rows
        ]

    def disks(self, target: Day2Target):
        config = self.configuration(target)
        rows = []
        for device, raw in sorted(config.items()):
            if not (_DISK_KEY.fullmatch(device) or _UNUSED_KEY.fullmatch(device)):
                continue
            opts = _parse_options(raw)
            volume = opts.get('volume') or ''
            storage = volume.split(':', 1)[0] if ':' in volume else None
            rows.append({
                'device': device,
                'size_gib': _size_gib(raw),
                'storage': storage,
                'bus': re.sub(r'\\d+$', '', device),
                'format': opts.get('format'),
                'boot': device in str(config.get('boot') or ''),
                'provider_id': volume or device,
                'status': 'detached' if device.startswith('unused') else 'attached',
                'raw': raw,
            })
        return rows

    def nics(self, target: Day2Target):
        config = self.configuration(target)
        addresses = self.guest_addresses(target)
        rows = []
        for device, raw in sorted(config.items()):
            if not _NIC_KEY.fullmatch(device):
                continue
            opts = _parse_options(raw)
            model = opts.get('model')
            mac = opts.get(model) if model and model in opts else None
            rows.append({
                'interface': device,
                'mac': mac,
                'network': opts.get('bridge'),
                'vlan': int(opts['tag']) if str(opts.get('tag', '')).isdigit() else None,
                'ip': addresses[0] if device == 'net0' and addresses else None,
                'model': model,
                'firewall': str(opts.get('firewall', '0')) == '1',
                'state': 'down' if str(opts.get('link_down', '0')) == '1' else 'up',
                'raw': raw,
            })
        return rows

    def snapshot_state(self, target: Day2Target):
        status = self.status(target)
        config = self.configuration(target)
        addresses = self.guest_addresses(target)
        return {
            'provider': 'proxmox',
            'node': target.node,
            'vm_id': target.vm_id,
            'name': status.get('name') or config.get('name') or target.name,
            'power_state': status.get('status') or 'unknown',
            'cpu_cores': config.get('cores') or status.get('cpus'),
            'cpu_sockets': config.get('sockets'),
            'memory_mb': config.get('memory'),
            'tags': [tag for tag in str(config.get('tags') or '').split(';') if tag],
            'primary_ip': addresses[0] if addresses else target.primary_ip,
            'configuration': _public_config(config),
            'disks': self.disks(target),
            'nics': self.nics(target),
        }

    def _network_value(self, params, current=None):
        current_opts = _parse_options(current) if current else {}
        model = params.get('model') or current_opts.get('model') or 'virtio'
        mac = params.get('mac')
        if mac is None and current_opts.get(model):
            mac = current_opts.get(model)
        head = model + (('=' + mac) if mac else '')
        parts = [head, 'bridge=' + params['bridge']]
        vlan = params.get('vlan')
        if vlan is not None:
            parts.append('tag=' + str(int(vlan)))
        if params.get('firewall'):
            parts.append('firewall=1')
        if params.get('link_up') is False:
            parts.append('link_down=1')
        return ','.join(parts)

    def _cloud_init_values(self, params):
        values = {}
        if 'hostname' in params:
            values['name'] = params['hostname']
        if 'dns_servers' in params:
            values['nameserver'] = ' '.join(params.get('dns_servers') or [])
        if 'search_domain' in params:
            values['searchdomain'] = params['search_domain']
        elif 'domain' in params:
            values['searchdomain'] = params['domain']
        if 'user' in params:
            values['ciuser'] = params['user']
        if 'ssh_public_keys' in params:
            values['sshkeys'] = '\\n'.join(params.get('ssh_public_keys') or [])
        if any(key in params for key in ('ipv4', 'ipv6', 'gateway')):
            parts = []
            if params.get('ipv4'):
                parts.append('ip=' + params['ipv4'])
            if params.get('ipv6'):
                parts.append('ip6=' + params['ipv6'])
            if params.get('gateway'):
                parts.append('gw=' + params['gateway'])
            values['ipconfig0'] = ','.join(parts)
        return values

    def execute(self, target: Day2Target, action: str, params: dict):
        node, vm_id = self._identity(target)
        power = {
            'power_on': 'start', 'power_off': 'stop', 'shutdown': 'shutdown', 'reboot': 'reboot',
            'reset': 'reset', 'suspend': 'suspend', 'resume': 'resume',
        }
        if action in power:
            return self.provider.vm_power(node, vm_id, power[action])
        if action == 'create_snapshot':
            if params.get('quiesce'):
                raise failure('ACTION_NOT_SUPPORTED', message='Proxmox adapter does not expose an explicit snapshot quiesce switch')
            return self.provider.create_snapshot(node, vm_id, params['name'], params.get('description', ''), bool(params.get('include_memory')))
        if action == 'delete_snapshot':
            return self.provider.delete_snapshot(node, vm_id, params['name'])
        if action == 'restore_snapshot':
            return self.provider.rollback_snapshot(node, vm_id, params['name'])
        if action == 'resize_compute':
            values = {}
            if params.get('cpu_cores') is not None:
                values['cores'] = int(params['cpu_cores'])
            if params.get('cpu_sockets') is not None:
                values['sockets'] = int(params['cpu_sockets'])
            if params.get('memory_mb') is not None:
                values['memory'] = int(params['memory_mb'])
            return self.provider.update_vm_config(node, vm_id, **values)
        if action == 'add_disk':
            parts = [f"{params['storage']}:{int(params['size_gib'])}"]
            if params.get('format'):
                parts.append('format=' + params['format'])
            if params.get('cache'):
                parts.append('cache=' + params['cache'])
            if params.get('discard'):
                parts.append('discard=on')
            if params.get('ssd_emulation'):
                parts.append('ssd=1')
            return self.provider.update_vm_config(node, vm_id, **{params['device']: ','.join(parts)})
        if action == 'resize_disk':
            config = self.configuration(target)
            device = params['device']
            if device not in config:
                raise failure('VALIDATION_FAILED', message='Selected disk does not exist', status_code=422)
            current = _size_gib(config[device])
            if current is None:
                raise failure('VALIDATION_FAILED', message='Current disk size cannot be determined safely', status_code=422)
            target_size = float(params['new_size_gib'])
            if target_size < current:
                raise failure('VALIDATION_FAILED', message='Disk shrinking is not supported', status_code=422)
            grow = int(math.ceil(target_size - current))
            return None if grow <= 0 else self.provider.resize_disk(node, vm_id, disk=device, grow_gib=grow)
        if action == 'detach_disk':
            return self.provider.delete_vm_config_key(node, vm_id, params['device'])
        if action == 'delete_disk':
            config = self.configuration(target)
            device = params['device']
            raw = str(config.get(device) or '')
            volume = raw.split(',', 1)[0]
            if not _UNUSED_KEY.fullmatch(device) or ':' not in volume:
                raise failure('VALIDATION_FAILED', message='Only an unused/detached Proxmox disk can be permanently deleted', status_code=422)
            storage = volume.split(':', 1)[0]
            self.provider.delete_vm_config_key(node, vm_id, device)
            self.provider.delete_storage_volume(node, storage, volume)
            return None
        if action in {'add_nic', 'edit_nic'}:
            config = self.configuration(target)
            device = params['device']
            current = config.get(device)
            if action == 'add_nic' and current is not None:
                raise failure('VALIDATION_FAILED', message='Selected NIC slot is already in use', status_code=422)
            if action == 'edit_nic' and current is None:
                raise failure('VALIDATION_FAILED', message='Selected NIC does not exist', status_code=422)
            return self.provider.update_vm_config(node, vm_id, **{device: self._network_value(params, current)})
        if action == 'detach_nic':
            return self.provider.delete_vm_config_key(node, vm_id, params['device'])
        if action == 'update_cloud_init':
            values = self._cloud_init_values(params)
            if not values:
                raise failure('VALIDATION_FAILED', message='No cloud-init values were supplied', status_code=422)
            return self.provider.update_vm_config(node, vm_id, **values)
        if action in {'update_tags', 'add_tag', 'remove_tag'}:
            config = self.configuration(target)
            tags = {tag for tag in str(config.get('tags') or '').split(';') if tag}
            if action == 'update_tags':
                tags = set(params.get('tags') or [])
            elif action == 'add_tag':
                tags.add(params['tag'])
            else:
                tags.discard(params['tag'])
            return self.provider.update_vm_config(node, vm_id, tags=';'.join(sorted(tags)))
        if action == 'migrate_vm':
            return self.provider.migrate_vm(node, vm_id, target=params['target_node'], online=bool(params.get('online')), with_local_disks=bool(params.get('with_local_disks')))
        if action == 'move_storage':
            return self.provider.move_disk(node, vm_id, disk=params['device'], storage=params['target_storage'], delete_source=bool(params.get('delete_source', True)))
        if action == 'clone_vm':
            return self.provider.clone_vm(node, vm_id, new_vm_id=int(params['new_vm_id']), name=params['name'], target=params.get('target_node'), full=bool(params.get('full', True)), storage=params.get('target_storage'))
        if action == 'delete_vm':
            return self.provider.delete_vm(node, vm_id, purge=bool(params.get('purge')), destroy_unreferenced_disks=bool(params.get('destroy_unreferenced_disks')))
        if action == 'refresh_state':
            return None
        raise failure('ACTION_NOT_SUPPORTED')

    def wait_task(self, target: Day2Target, task, check, timeout=3600):
        if not isinstance(task, str) or not task.startswith('UPID:'):
            return {'provider_task_id': task if isinstance(task, str) else None}
        node, _ = self._identity(target)
        started = time.monotonic()
        while True:
            check()
            status = self.provider.task_status(node, task) or {}
            if status.get('status') == 'stopped':
                exit_status = status.get('exitstatus')
                if exit_status not in {None, 'OK'}:
                    raise failure('PROVIDER_ERROR', message='Provider task failed', details={'exit_status': str(exit_status)[:128]})
                return {'provider_task_id': task, 'provider_task_status': exit_status or 'OK'}
            if time.monotonic() - started > timeout:
                raise failure('TIMEOUT')
            time.sleep(1)

    def cancel_task(self, target: Day2Target, task):
        if not isinstance(task, str) or not task.startswith('UPID:'):
            return False
        node, _ = self._identity(target)
        try:
            self.provider.stop_task(node, task)
            return True
        except HTTPException:
            return False


class UnsupportedDay2Adapter:
    def __init__(self, provider_type):
        self.provider_type = provider_type

    def capabilities(self, target):
        return {'provider': self.provider_type, 'actions': [], 'console': False}

    def status(self, target):
        return {'status': 'unknown'}

    def snapshot_state(self, target):
        return {'provider': self.provider_type, 'power_state': 'unknown'}

    def guest_addresses(self, target):
        return [target.primary_ip] if target.primary_ip else []


def day2_provider(credential: Credential):
    if credential.type == 'proxmox':
        return ProxmoxDay2Adapter(credential)
    return UnsupportedDay2Adapter(credential.type)
