import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from fastapi import HTTPException

from app.onboarding.providers.base import NormalizedDiscoveryResource, OnboardingProviderAdapter
from app.providers.proxmox import ProxmoxProvider


_DISK_KEY = re.compile(r'^(?:scsi|virtio|sata|ide|efidisk|tpmstate|unused)\d+$')
_LXC_DISK_KEY = re.compile(r'^(?:rootfs|mp\d+)$')
_NIC_KEY = re.compile(r'^net\d+$')
_SENSITIVE_KEY = re.compile(r'(?:pass|secret|token|private[_-]?key|cipassword)', re.I)
_UUID = re.compile(r'(?:^|,)uuid=([^,]+)', re.I)


def _redact(value):
    if isinstance(value, dict):
        return {
            str(key): ('********' if _SENSITIVE_KEY.search(str(key)) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _tags(value):
    if isinstance(value, list):
        return sorted({str(item).strip() for item in value if str(item).strip()})
    return sorted({
        item.strip()
        for item in str(value or '').replace(',', ';').split(';')
        if item.strip()
    })


def _options(value):
    result = {}
    parts = [item.strip() for item in str(value or '').split(',') if item.strip()]
    if parts and '=' not in parts[0]:
        result['volume'] = parts[0]
    for index, part in enumerate(parts):
        if '=' in part:
            key, item = part.split('=', 1)
            result[key] = item
            if index == 0 and key in {'virtio', 'e1000', 'e1000e', 'rtl8139', 'vmxnet3'}:
                result['model'] = key
                result['mac'] = item
        elif index == 0:
            result.setdefault('model', part)
    return result


def _disk_row(device, raw):
    opts = _options(raw)
    volume = opts.get('volume') or opts.get('file') or ''
    storage = volume.split(':', 1)[0] if ':' in volume else None
    return {
        'device': device,
        'storage': storage,
        'volume': volume or None,
        'size': opts.get('size'),
        'format': opts.get('format'),
        'media': opts.get('media'),
        'backup': opts.get('backup'),
        'replicate': opts.get('replicate'),
        'raw': str(raw),
    }


def _qemu_nic(device, raw):
    opts = _options(raw)
    model = opts.get('model')
    mac = opts.get('mac') or (opts.get(model) if model else None)
    return {
        'interface': device,
        'model': model,
        'mac': str(mac).lower() if mac else None,
        'bridge': opts.get('bridge'),
        'vlan': int(opts['tag']) if str(opts.get('tag', '')).isdigit() else None,
        'firewall': str(opts.get('firewall', '0')) == '1',
        'link_up': str(opts.get('link_down', '0')) != '1',
        'raw': str(raw),
    }


def _lxc_nic(device, raw):
    opts = _options(raw)
    return {
        'interface': opts.get('name') or device,
        'model': 'veth',
        'mac': str(opts.get('hwaddr') or '').lower() or None,
        'bridge': opts.get('bridge'),
        'vlan': int(opts['tag']) if str(opts.get('tag', '')).isdigit() else None,
        'firewall': str(opts.get('firewall', '0')) == '1',
        'link_up': True,
        'raw': str(raw),
    }


def _memory_mb(raw, config):
    value = config.get('memory')
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(int(raw.get('maxmem') or 0) / 1024 / 1024) or None
    except (TypeError, ValueError):
        return None


def _cpu_count(raw, config):
    try:
        cores = int(config.get('cores') or 0)
        sockets = int(config.get('sockets') or 1)
        if cores:
            return cores * sockets
    except (TypeError, ValueError):
        pass
    for key in ('maxcpu', 'cpus'):
        try:
            value = int(raw.get(key) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return None


class ProxmoxOnboardingAdapter(OnboardingProviderAdapter):
    provider_type = 'proxmox'

    def __init__(self, provider_row, infrastructure_adapter):
        if not isinstance(infrastructure_adapter, ProxmoxProvider):
            raise HTTPException(422, 'Proxmox onboarding requires a Proxmox infrastructure adapter')
        super().__init__(provider_row, infrastructure_adapter)

    @classmethod
    def static_capabilities(cls):
        return {
            'supports_discovery': True,
            'supports_vm_import': True,
            'supports_guest_agent': True,
            'supports_snapshots': True,
            'supports_power_actions': True,
            'supports_console': True,
            'supports_network_inspection': True,
            'supports_storage_inspection': True,
            'supports_guest_discovery': True,
            'resource_types': ['qemu', 'lxc'],
            'modes': ['DISCOVER_ONLY', 'INVENTORY_IMPORT', 'MANAGED', 'FULL_ADOPTION'],
        }

    def _cluster_id(self):
        try:
            rows = self.provider._get('/cluster/status') or []
        except HTTPException:
            rows = []
        cluster = next(
            (row for row in rows if str(row.get('type') or '').lower() == 'cluster'),
            None,
        )
        value = (cluster or {}).get('name') or (cluster or {}).get('id')
        return str(value or ('provider-' + str(self.provider_row.id)))

    def _ha(self):
        try:
            rows = self.provider._get('/cluster/ha/resources') or []
        except HTTPException:
            return {}
        return {str(row.get('sid') or ''): row for row in rows if row.get('sid')}

    def _pool_rows(self):
        try:
            return self.provider._get('/pools') or []
        except HTTPException:
            return []

    def _pool_membership(self):
        membership = {}
        for pool in self._pool_rows():
            poolid = str(pool.get('poolid') or '')
            if not poolid:
                continue
            try:
                details = self.provider._get('/pools/' + quote(poolid, safe='')) or {}
            except HTTPException:
                continue
            for member in details.get('members') or []:
                vmid = member.get('vmid')
                member_type = str(member.get('type') or '').lower()
                if vmid is not None and member_type in {'qemu', 'lxc'}:
                    membership[(member_type, int(vmid))] = poolid
        return membership

    def _agent_state(self, node, vmid, config):
        raw = str(config.get('agent') or '').lower()
        enabled = raw in {'1', 'true', 'yes', 'on'} or 'enabled=1' in raw
        if not enabled:
            return 'DISABLED'
        try:
            return 'RUNNING' if self.provider.guest_agent_ready(node, vmid) else 'ENABLED_NOT_RESPONDING'
        except HTTPException:
            return 'ENABLED_NOT_RESPONDING'
        except Exception:
            return 'UNKNOWN'

    def _guest_os(self, node, vmid, agent_state, config):
        result = {
            'os': config.get('ostype'),
            'agent_status': agent_state,
        }
        if agent_state != 'RUNNING':
            return result
        try:
            payload = self.provider._get(
                f'/nodes/{quote(node, safe="")}/qemu/{int(vmid)}/agent/get-osinfo'
            ) or {}
            info = payload.get('result') if isinstance(payload.get('result'), dict) else payload
            if isinstance(info, dict):
                result.update({
                    'name': info.get('name'),
                    'pretty_name': info.get('pretty-name'),
                    'version': info.get('version'),
                    'version_id': info.get('version-id'),
                    'kernel_release': info.get('kernel-release'),
                    'kernel_version': info.get('kernel-version'),
                    'machine': info.get('machine'),
                })
        except Exception:
            pass
        return result

    def _addresses(self, resource_type, node, vmid, agent_state):
        if resource_type == 'qemu':
            if agent_state != 'RUNNING':
                return []
            try:
                return self.provider.guest_addresses(node, vmid)
            except Exception:
                return []
        try:
            rows = self.provider._get(
                f'/nodes/{quote(node, safe="")}/lxc/{int(vmid)}/interfaces'
            ) or []
        except Exception:
            return []
        result = []
        for row in rows:
            for key in ('inet', 'inet6'):
                raw = str(row.get(key) or '').split('/', 1)[0].strip()
                if raw and not raw.startswith(('127.', '::1', 'fe80:')):
                    result.append(raw)
        return sorted(set(result))

    def _details(self, raw, cluster_id, ha, pools):
        resource_type = str(raw.get('type') or '').lower()
        node = str(raw.get('node') or '')
        vmid = int(raw.get('vmid'))
        base = f'/nodes/{quote(node, safe="")}/{resource_type}/{vmid}'
        try:
            config = (
                self.provider.vm_config(node, vmid)
                if resource_type == 'qemu'
                else self.provider._get(base + '/config')
            ) or {}
        except HTTPException:
            config = {}
        try:
            status = (
                self.provider.vm_status(node, vmid)
                if resource_type == 'qemu'
                else self.provider._get(base + '/status/current')
            ) or {}
        except HTTPException:
            status = {}
        try:
            snapshots = (
                self.provider.snapshots(node, vmid)
                if resource_type == 'qemu'
                else self.provider._get(base + '/snapshot')
            ) or []
        except HTTPException:
            snapshots = []

        agent_state = (
            self._agent_state(node, vmid, config)
            if resource_type == 'qemu'
            else 'UNKNOWN'
        )
        addresses = self._addresses(resource_type, node, vmid, agent_state)
        return {
            'cluster_id': cluster_id,
            'config': config,
            'status': status,
            'snapshots': snapshots,
            'ha': ha.get('vm:' + str(vmid)),
            'pool': pools.get((resource_type, vmid)) or raw.get('pool'),
            'agent_state': agent_state,
            'addresses': addresses,
        }

    def _matches_scope(self, raw, scope, pools):
        resource_type = str(raw.get('type') or '').lower()
        if resource_type not in set(scope.get('resource_types') or ['qemu', 'lxc']):
            return False
        if not scope.get('include_templates', True) and bool(raw.get('template')):
            return False
        nodes = {str(item) for item in scope.get('nodes') or []}
        if nodes and str(raw.get('node') or '') not in nodes:
            return False
        try:
            vmid = int(raw.get('vmid'))
        except (TypeError, ValueError):
            return False
        vm_ids = {int(value) for value in scope.get('vm_ids') or []}
        if vm_ids and vmid not in vm_ids:
            return False
        if scope.get('vmid_min') is not None and vmid < int(scope['vmid_min']):
            return False
        if scope.get('vmid_max') is not None and vmid > int(scope['vmid_max']):
            return False
        pool = str(scope.get('pool') or '')
        if pool and str(pools.get((resource_type, vmid)) or raw.get('pool') or '') != pool:
            return False
        required_tags = {str(item).casefold() for item in scope.get('tags') or []}
        row_tags = {item.casefold() for item in _tags(raw.get('tags'))}
        if required_tags and not required_tags <= row_tags:
            return False
        return True

    def discover_resources(self, scope, *, concurrency=5, cancelled=None):
        rows = self.provider._get('/cluster/resources?type=vm') or []
        pools = self._pool_membership()
        selected = [
            row for row in rows
            if str(row.get('type') or '').lower() in {'qemu', 'lxc'}
            and self._matches_scope(row, scope or {}, pools)
        ]
        cluster_id = self._cluster_id()
        requested_cluster = str((scope or {}).get('cluster') or '')
        if requested_cluster and requested_cluster != cluster_id:
            return []
        ha = self._ha()
        limit = max(1, min(16, int(concurrency or 5)))
        results = []
        with ThreadPoolExecutor(max_workers=min(limit, max(1, len(selected)))) as executor:
            futures = {
                executor.submit(self._details, row, cluster_id, ha, pools): row
                for row in selected
            }
            for future in as_completed(futures):
                if cancelled and cancelled():
                    for pending in futures:
                        pending.cancel()
                    break
                row = futures[future]
                try:
                    details = future.result()
                except Exception as exc:
                    details = {
                        'cluster_id': cluster_id,
                        'config': {},
                        'status': {},
                        'snapshots': [],
                        'ha': None,
                        'pool': pools.get((str(row.get('type') or ''), int(row.get('vmid')))),
                        'agent_state': 'UNKNOWN',
                        'addresses': [],
                        'detail_error': exc.__class__.__name__,
                    }
                results.append(self.normalize_resource(row, details))
        return sorted(results, key=lambda item: (item.location.get('node') or '', int(item.external_id)))

    def normalize_resource(self, raw, details=None):
        details = details or {}
        config = details.get('config') or {}
        status = details.get('status') or {}
        resource_type = str(raw.get('type') or '').lower()
        vmid = int(raw.get('vmid'))
        node = str(raw.get('node') or status.get('node') or '')
        disks = []
        networks = []
        for key, value in config.items():
            if resource_type == 'qemu' and _DISK_KEY.fullmatch(key):
                disks.append(_disk_row(key, value))
            elif resource_type == 'lxc' and _LXC_DISK_KEY.fullmatch(key):
                disks.append(_disk_row(key, value))
            elif _NIC_KEY.fullmatch(key):
                networks.append(
                    _qemu_nic(key, value)
                    if resource_type == 'qemu'
                    else _lxc_nic(key, value)
                )
        raw_tags = config.get('tags') or raw.get('tags')
        agent_state = str(details.get('agent_state') or 'UNKNOWN')
        guest = (
            self._guest_os(node, vmid, agent_state, config)
            if resource_type == 'qemu'
            else {'os': config.get('ostype'), 'agent_status': 'UNKNOWN'}
        )
        smbios = str(config.get('smbios1') or '')
        uuid_match = _UUID.search(smbios)
        provider_uuid = uuid_match.group(1) if uuid_match else None
        is_template = bool(raw.get('template') or config.get('template'))
        cloud_init = any(
            'cloudinit' in str(config.get(key) or '').lower()
            for key in ('ide0', 'ide1', 'ide2', 'ide3', 'scsi0', 'sata0')
        ) or any(key in config for key in ('ciuser', 'ipconfig0', 'cicustom'))
        name = str(config.get('name') or raw.get('name') or ('vm-' + str(vmid)))
        capabilities = self.get_capabilities(None)
        if resource_type == 'lxc':
            capabilities = {
                **capabilities,
                'supports_guest_agent': False,
                'supports_console': False,
                'supports_power_actions': False,
                'supports_snapshots': False,
            }
        if is_template:
            capabilities = {**capabilities, 'supports_power_actions': False}

        raw_metadata = _redact({
            'cluster_resource': raw,
            'configuration': config,
            'runtime': status,
            'ha': details.get('ha'),
            'snapshots': details.get('snapshots') or [],
            'detail_error': details.get('detail_error'),
        })
        storage = sorted({
            row.get('storage') for row in disks if row.get('storage')
        })
        return NormalizedDiscoveryResource(
            external_id=str(vmid),
            provider_id=int(self.provider_row.id),
            resource_type=resource_type,
            name=name,
            hostname=name,
            power_state=str(status.get('status') or raw.get('status') or 'unknown').lower(),
            cpu=_cpu_count(raw, config),
            memory_mb=_memory_mb(raw, config),
            disks=disks,
            networks=networks,
            addresses=list(details.get('addresses') or []),
            tags=_tags(raw_tags),
            location={
                'cluster': str(details.get('cluster_id') or ('provider-' + str(self.provider_row.id))),
                'node': node,
                'pool': details.get('pool'),
                'storage': storage,
                'ha': details.get('ha'),
                'node_affinity': node or None,
            },
            guest=guest,
            capabilities=capabilities,
            raw_metadata=raw_metadata,
            provider_uuid=provider_uuid,
            is_template=is_template,
            uptime=int(status.get('uptime') or raw.get('uptime') or 0) or None,
            snapshots=[
                {
                    'name': item.get('name'),
                    'description': item.get('description') or '',
                    'created': item.get('snaptime'),
                    'parent': item.get('parent'),
                }
                for item in (details.get('snapshots') or [])
                if item.get('name') != 'current'
            ],
            description=str(config.get('description') or raw.get('description') or ''),
            bios=config.get('bios'),
            machine_type=config.get('machine'),
            cloud_init=cloud_init,
        )

    def get_resource(self, *, resource_type, external_id):
        try:
            vmid = int(external_id)
        except (TypeError, ValueError):
            return None
        rows = self.provider._get('/cluster/resources?type=vm') or []
        raw = next(
            (
                row for row in rows
                if str(row.get('type') or '').lower() == str(resource_type).lower()
                and int(row.get('vmid') or -1) == vmid
            ),
            None,
        )
        if raw is None:
            return None
        pools = self._pool_membership()
        details = self._details(raw, self._cluster_id(), self._ha(), pools)
        return self.normalize_resource(raw, details)

    def get_external_identity(self, resource):
        return {
            'provider_id': int(self.provider_row.id),
            'cluster_id': str(resource.location.get('cluster') or ('provider-' + str(self.provider_row.id))),
            'node_id': str(resource.location.get('node') or ''),
            'resource_type': resource.resource_type,
            'external_id': str(resource.external_id),
            'provider_uuid': resource.provider_uuid,
        }

    def get_capabilities(self, resource=None):
        return self.static_capabilities()

    def get_guest_agent_status(self, resource):
        return str((resource.guest or {}).get('agent_status') or 'UNKNOWN')

    def refresh_resource(self, identity):
        return self.get_resource(
            resource_type=identity.resource_type,
            external_id=identity.external_id,
        )

    def validate_adoption(self, resource, mode):
        messages = []
        if resource.is_template and mode in {'MANAGED', 'FULL_ADOPTION'}:
            messages.append({
                'severity': 'error',
                'code': 'TEMPLATE_MANAGED_ADOPTION_UNSUPPORTED',
                'message': 'Template can be discovered or imported to inventory, but cannot be adopted as a running managed VM.',
            })
        if resource.resource_type == 'lxc' and mode == 'FULL_ADOPTION':
            messages.append({
                'severity': 'warning',
                'code': 'LXC_FULL_ADOPTION_LIMITED',
                'message': 'LXC can be inventoried, but QEMU Guest Agent and QEMU-specific Day-2 actions are unavailable.',
            })
        if mode == 'FULL_ADOPTION' and self.get_guest_agent_status(resource) != 'RUNNING':
            messages.append({
                'severity': 'warning',
                'code': 'GUEST_AGENT_NOT_RUNNING',
                'message': 'Full adoption can continue, but guest discovery requires credentials or a working guest agent.',
            })
        return messages

    def permission_diagnostics(self):
        checks = []
        try:
            rows = self.provider._get('/cluster/resources?type=vm') or []
            checks.append({'id': 'vm_read', 'label': 'VM read', 'status': 'pass', 'message': f'VM inventory readable ({len(rows)} resources).'})
        except HTTPException as exc:
            return [{
                'id': 'vm_read', 'label': 'VM read', 'status': 'fail',
                'message': 'Provider authentication works but VM inventory is not readable.',
                'details': {'http_status': exc.status_code},
            }]
        sample = next((row for row in rows if str(row.get('type') or '').lower() in {'qemu', 'lxc'}), None)
        if sample is None:
            checks.append({'id': 'vm_config_read', 'label': 'VM config read', 'status': 'skipped', 'message': 'No VM is available for a configuration permission probe.'})
            return checks
        node = str(sample.get('node') or '')
        vmid = int(sample.get('vmid'))
        resource_type = str(sample.get('type') or '').lower()
        try:
            if resource_type == 'qemu':
                self.provider.vm_config(node, vmid)
            else:
                self.provider._get(f'/nodes/{quote(node, safe="")}/lxc/{vmid}/config')
            checks.append({'id': 'vm_config_read', 'label': 'VM config read', 'status': 'pass', 'message': 'VM configuration API is readable.'})
        except HTTPException as exc:
            checks.append({
                'id': 'vm_config_read', 'label': 'VM config read', 'status': 'fail',
                'message': 'VM list is readable, but VM configuration is not.',
                'details': {'http_status': exc.status_code, 'sample_vmid': vmid},
            })
        return checks
