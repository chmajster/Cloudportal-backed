import math
import re
import time

from fastapi import HTTPException
from sqlalchemy import select

from app.database import session
from app.executors.base import Cancelled, ExecutionFailed
from app.models import Deployment, Job, ManagedResource, ManagedVM
from app.providers.registry import provider_for
from app.terraform.identity import reserve_proxmox_vm_id


RUNTIME_KEY = '_proxmox_provision_runtime'
POLL_SECONDS = 2
PERCENT = re.compile(r'(?<![0-9.])(100(?:\.0+)?|[0-9]{1,2}(?:\.[0-9]+)?)\s*%')


def parse_task_progress(rows):
    """Return the newest real percentage printed by a Proxmox task, or None."""
    latest = None
    for row in rows or []:
        text = str(row.get('t') if isinstance(row, dict) else row)
        for match in PERCENT.finditer(text):
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 100:
                latest = value
    return latest


def _runtime(context):
    return dict((context.job.payload or {}).get(RUNTIME_KEY) or {})


def _persist(context, **changes):
    with session() as db:
        job = db.get(Job, context.job.id)
        if job is None:
            raise ExecutionFailed('Proxmox provisioning job disappeared')
        payload = dict(job.payload or {})
        runtime = dict(payload.get(RUNTIME_KEY) or {})
        runtime.update(changes)
        payload[RUNTIME_KEY] = runtime
        job.payload = payload
        context.job.payload = dict(payload)
        db.commit()
    return runtime


def provider(context):
    return provider_for(context.credential)


def _status_or_none(adapter, node, vm_id):
    try:
        value = adapter.vm_status(node, int(vm_id)) or {}
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    return value if isinstance(value, dict) else {}


def identity(context):
    deployment = context.deployment
    variables = dict(deployment.variables or {})
    node = str(variables.get('node') or '').strip()
    vm_id = variables.get('vm_id') or variables.get('vmid')
    if not node:
        raise ExecutionFailed('Proxmox node missing from deployment variables')
    if vm_id in {None, ''}:
        raise ExecutionFailed('Proxmox VMID has not been reserved yet')
    try:
        return node, int(vm_id), provider(context)
    except (TypeError, ValueError):
        raise ExecutionFailed('Proxmox VMID is invalid') from None


def task_node(upid, fallback):
    parts = str(upid or '').split(':')
    if len(parts) > 1 and re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', parts[1] or ''):
        return parts[1]
    return fallback


def _task_log(adapter, node, upid):
    try:
        return adapter.task_log(node, upid, start=0, limit=2000)
    except Exception:
        # Progress telemetry must never turn a healthy provider task into failure.
        return []


def wait_task(context, adapter, node, upid, label, *, phase, timeout=7200):
    if not isinstance(upid, str) or not upid:
        return {}
    deadline = time.monotonic() + max(1, int(timeout))
    last_percent = None
    try:
        while time.monotonic() < deadline:
            context.check()
            status = adapter.task_status(node, upid) or {}
            percent = parse_task_progress(_task_log(adapter, node, upid))
            if percent is not None and percent != last_percent:
                last_percent = percent
                context.progress(percent, label, phase=phase)

            stopped = (
                str(status.get('status') or '').lower() == 'stopped'
                or bool(status.get('exitstatus'))
            )
            if stopped:
                exit_status = str(status.get('exitstatus') or '')
                if exit_status != 'OK':
                    raise ExecutionFailed(
                        f'{label} zakończył się błędem Proxmox: '
                        + (exit_status or 'nieznany status')
                    )
                context.progress(100, label + ' — zakończone', phase=phase)
                return status
            time.sleep(POLL_SECONDS)
    except Cancelled:
        try:
            adapter.stop_task(node, upid)
        except Exception:
            pass
        raise
    raise ExecutionFailed(f'Przekroczono czas oczekiwania na operację Proxmox: {label}')


def _wait_target(context, adapter, node, vm_id, timeout=180):
    deadline = time.monotonic() + max(1, int(timeout))
    while time.monotonic() < deadline:
        context.check()
        try:
            status = _status_or_none(adapter, node, vm_id)
        except Exception:
            status = None
        if status is not None:
            return status
        time.sleep(POLL_SECONDS)
    raise ExecutionFailed(f'VM {node}/{vm_id} nie pojawiła się w Proxmox po zakończeniu klonowania')


def ensure_vmid(context):
    deployment = context.deployment
    variables = dict(deployment.variables or {})
    current = variables.get('vm_id') or variables.get('vmid')
    if current not in {None, ''}:
        try:
            return int(current)
        except (TypeError, ValueError):
            raise ExecutionFailed('Configured Proxmox VMID is invalid') from None

    vm_id = reserve_proxmox_vm_id(
        deployment.id,
        context.credential,
        context=context,
    )
    variables['vm_id'] = int(vm_id)
    deployment.variables = variables
    context.deployment.variables = dict(variables)
    context.log(f'proxmox.provision.vmid.reserved: {vm_id}')
    return int(vm_id)


def clone(context, timeout=7200):
    deployment = context.deployment
    variables = dict(deployment.variables or {})
    vm_id = ensure_vmid(context)
    target_node = str(variables.get('node') or '').strip()
    source_node = str(variables.get('template_node') or target_node).strip()
    try:
        source_vm_id = int(variables['template_id'])
    except (KeyError, TypeError, ValueError):
        raise ExecutionFailed('Proxmox source template VMID is missing or invalid') from None
    if not target_node or not source_node:
        raise ExecutionFailed('Proxmox source or target node is missing')

    adapter = provider(context)
    runtime = _runtime(context)
    target = _status_or_none(adapter, target_node, vm_id)

    if runtime.get('clone_completed') is True and target is not None:
        context.progress(100, 'Klonowanie VM — zakończone', phase='clone')
        return vm_id

    upid = runtime.get('clone_upid')
    if upid:
        context.quota_provider_submitted = True
        context.stage('proxmox.provision.clone')
        context.progress(None, 'Klonowanie VM w Proxmox', phase='clone')
        wait_task(
            context, adapter, str(runtime.get('clone_task_node') or task_node(upid, source_node)),
            upid, 'Klonowanie VM', phase='clone', timeout=timeout,
        )
        _wait_target(context, adapter, target_node, vm_id)
        _persist(context, clone_completed=True)
        return vm_id

    if target is not None:
        raise ExecutionFailed(
            f'Docelowy VMID {vm_id} jest już zajęty w Proxmox, a job nie ma checkpointu klonowania'
        )

    context.stage('proxmox.provision.clone')
    context.progress(None, 'Uruchamianie pełnego klonowania VM w Proxmox', phase='clone')
    try:
        upid = adapter.clone_vm(
            source_node,
            source_vm_id,
            new_vm_id=vm_id,
            name=deployment.name,
            target=target_node,
            full=True,
            storage=variables.get('storage') or None,
            pool=variables.get('pool') or None,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else f'HTTP {exc.status_code}'
        raise ExecutionFailed('Nie udało się uruchomić klonowania VM w Proxmox: ' + detail[:300]) from None
    if not isinstance(upid, str) or not upid:
        raise ExecutionFailed('Proxmox nie zwrócił UPID dla operacji klonowania')

    context.quota_provider_submitted = True
    _persist(
        context,
        clone_requested=True,
        clone_upid=upid,
        clone_source_node=source_node,
        clone_task_node=task_node(upid, source_node),
        target_node=target_node,
        target_vm_id=vm_id,
    )
    context.log(
        f'proxmox.provision.clone.requested: source={source_node}/{source_vm_id} '
        f'target={target_node}/{vm_id} upid={upid}'
    )
    wait_task(
        context, adapter, task_node(upid, source_node), upid, 'Klonowanie VM',
        phase='clone', timeout=timeout,
    )
    _wait_target(context, adapter, target_node, vm_id)
    _persist(context, clone_completed=True)
    return vm_id


def _network_value(raw, bridge, vlan_id=None):
    raw = str(raw or '').strip()
    parts = [part for part in raw.split(',') if part] if raw else ['virtio']
    head = parts[0]
    options = {}
    passthrough = []
    for part in parts[1:]:
        if '=' not in part:
            passthrough.append(part)
            continue
        key, value = part.split('=', 1)
        if key not in {'bridge', 'tag'}:
            options[key] = value
    values = [head, 'bridge=' + str(bridge)]
    if vlan_id not in {None, ''}:
        values.append('tag=' + str(int(vlan_id)))
    values.extend(f'{key}={value}' for key, value in options.items())
    values.extend(passthrough)
    return ','.join(values)


def _disk_size_gib(raw):
    match = re.search(r'(?:^|,)size=([0-9]+(?:\.[0-9]+)?)([KMGT])(?:,|$)', str(raw or ''), re.I)
    if not match:
        return None
    value = float(match.group(1))
    factor = {'K': 1 / (1024 * 1024), 'M': 1 / 1024, 'G': 1, 'T': 1024}[match.group(2).upper()]
    return value * factor


def _primary_disk(current):
    def usable(key):
        raw = str(current.get(key) or '')
        return bool(raw) and 'media=cdrom' not in raw.lower()

    boot = str(current.get('boot') or '')
    order = re.search(r'order=([^;\s]+(?:;[^\s]+)*)', boot)
    if order:
        for key in order.group(1).split(';'):
            if re.fullmatch(r'(?:scsi|virtio|sata|ide)\d+', key) and usable(key):
                return key
    for prefix in ('scsi', 'virtio', 'sata', 'ide'):
        for index in range(32):
            key = f'{prefix}{index}'
            if usable(key):
                return key
    return None


def configure(context, timeout=1800):
    node, vm_id, adapter = identity(context)
    variables = dict(context.deployment.variables or {})
    context.stage('proxmox.provision.configure')
    context.progress(None, 'Konfiguracja CPU, RAM, sieci i dysku', phase='configure')

    try:
        current = adapter.vm_config(node, vm_id) or {}
    except Exception:
        raise ExecutionFailed('Nie udało się odczytać konfiguracji sklonowanej VM') from None

    values = {
        'name': context.deployment.name,
        'cores': int(variables.get('cpu') or 2),
        'memory': int(variables.get('memory') or 4096),
    }
    tags = [str(value).strip() for value in (variables.get('tags') or []) if str(value).strip()]
    if tags:
        values['tags'] = ';'.join(sorted(set(tags)))
    bridge = str(variables.get('network') or 'vmbr0')
    values['net0'] = _network_value(current.get('net0'), bridge, variables.get('vlan_id'))
    if variables.get('install_qemu_guest_agent'):
        values['agent'] = '1'

    try:
        task = adapter.update_vm_config(node, vm_id, **values)
        if task:
            wait_task(
                context, adapter, task_node(task, node), task, 'Konfiguracja VM',
                phase='configure', timeout=timeout,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else f'HTTP {exc.status_code}'
        raise ExecutionFailed('Konfiguracja VM w Proxmox nie powiodła się: ' + detail[:300]) from None

    desired_disk = float(variables.get('disk') or 0)
    if desired_disk > 0:
        disk_key = _primary_disk(current)
        if not disk_key:
            raise ExecutionFailed('Nie udało się wykryć głównego dysku sklonowanej VM')
        current_disk = _disk_size_gib(current.get(disk_key))
        if current_disk is None:
            raise ExecutionFailed(
                f'Nie udało się odczytać rozmiaru głównego dysku {disk_key}; desired state nie może zostać potwierdzony'
            )
        if desired_disk + 0.01 < current_disk:
            raise ExecutionFailed(
                f'Żądany dysk {desired_disk:.2f} GiB jest mniejszy od bieżącego '
                f'{current_disk:.2f} GiB na {disk_key}; Proxmox nie obsługuje bezpiecznego shrink'
            )
        if desired_disk > current_disk + 0.01:
            grow = max(1, int(math.ceil(desired_disk - current_disk)))
            task = adapter.resize_disk(node, vm_id, disk=disk_key, grow_gib=grow)
            if task:
                wait_task(
                    context, adapter, task_node(task, node), task, 'Powiększanie dysku VM',
                    phase='configure', timeout=timeout,
                )

    _persist(context, configured=True)
    return True


def configure_cloud_init(context, guest=None, timeout=1800):
    node, vm_id, adapter = identity(context)
    variables = dict(context.deployment.variables or {})
    blueprint = dict((context.job.payload or {}).get('blueprint') or {})
    account_mode = str(blueprint.get('guest_account_mode') or 'cloud_init_managed')

    context.stage('proxmox.provision.cloud_init')
    context.progress(None, 'Konfiguracja Cloud-init przez Proxmox API', phase='cloud_init')
    values = {}

    if account_mode == 'cloud_init_managed':
        username = str((guest or {}).get('username') or variables.get('ssh_username') or 'clouduser').strip()
        if username:
            values['ciuser'] = username
        public_key = (guest or {}).get('public_key') or variables.get('ssh_public_key')
        if public_key:
            values['sshkeys'] = str(public_key)
        password = (guest or {}).get('password')
        if password:
            values['cipassword'] = str(password)

    address = str(variables.get('ipv4_address') or '').strip()
    gateway = str(variables.get('ipv4_gateway') or '').strip()
    if address:
        values['ipconfig0'] = 'ip=' + address + (',gw=' + gateway if gateway else '')
    else:
        values['ipconfig0'] = 'ip=dhcp'

    dns_servers = [str(value).strip() for value in (variables.get('dns_servers') or []) if str(value).strip()]
    if dns_servers:
        values['nameserver'] = ' '.join(dns_servers)
    if variables.get('dns_domain'):
        values['searchdomain'] = str(variables['dns_domain'])

    try:
        task = adapter.update_vm_config(node, vm_id, **values)
        if task:
            wait_task(
                context, adapter, task_node(task, node), task, 'Konfiguracja Cloud-init',
                phase='cloud_init', timeout=timeout,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else f'HTTP {exc.status_code}'
        raise ExecutionFailed('Konfiguracja Cloud-init w Proxmox nie powiodła się: ' + detail[:300]) from None

    if variables.get('install_qemu_guest_agent'):
        context.log(
            'proxmox.provision.qemu_agent: Proxmox agent channel enabled; '
            'guest package installation still depends on the source template/cloud-init image'
        )
    _persist(context, cloud_init_configured=True)
    return True


def start(context, timeout=600):
    node, vm_id, adapter = identity(context)
    context.stage('proxmox.provision.start')
    context.progress(None, 'Uruchamianie VM w Proxmox', phase='start')
    current = _status_or_none(adapter, node, vm_id)
    if current is None:
        raise ExecutionFailed('VM zniknęła z Proxmox przed uruchomieniem')
    if str(current.get('status') or '').lower() == 'running':
        context.progress(100, 'VM uruchomiona', phase='start')
        _persist(context, started=True)
        return True

    task = adapter.vm_power(node, vm_id, 'start')
    if task:
        wait_task(
            context, adapter, task_node(task, node), task, 'Uruchamianie VM',
            phase='start', timeout=timeout,
        )

    deadline = time.monotonic() + max(1, int(timeout))
    while time.monotonic() < deadline:
        context.check()
        current = _status_or_none(adapter, node, vm_id)
        if current is not None and str(current.get('status') or '').lower() == 'running':
            context.progress(100, 'VM uruchomiona', phase='start')
            _persist(context, started=True)
            return True
        time.sleep(POLL_SECONDS)
    raise ExecutionFailed('VM nie osiągnęła stanu running po poleceniu start')


def register_inventory(context):
    node, vm_id, _adapter = identity(context)
    deployment = context.deployment
    variables = dict(deployment.variables or {})
    primary_ip = None
    raw_ip = str(variables.get('ipv4_address') or '').strip()
    if raw_ip:
        primary_ip = raw_ip.split('/', 1)[0]

    with session() as db:
        row = db.get(Deployment, deployment.id)
        if row is None:
            raise ExecutionFailed('Deployment disappeared before Proxmox inventory registration')

        by_identity = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == row.provider_id,
            ManagedVM.vm_id == vm_id,
        ))
        by_deployment = db.scalar(select(ManagedVM).where(
            ManagedVM.deployment_id == row.id,
        ))
        if by_identity is not None and by_identity.deployment_id not in {None, row.id}:
            raise ExecutionFailed('VM identity is already linked to another deployment')
        if by_identity is not None and by_deployment is not None and by_identity.id != by_deployment.id:
            raise ExecutionFailed('Deployment inventory points to a different VM identity')

        managed = by_identity or by_deployment
        if managed is None:
            managed = ManagedVM(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                provider_id=row.provider_id,
                deployment_id=row.id,
                node=node,
                vm_id=vm_id,
                name=row.name,
                management_mode='proxmox',
                lifecycle_status='active',
                created_by=row.created_by,
            )
            db.add(managed)
        else:
            managed.tenant_id = row.tenant_id
            managed.project_id = row.project_id
            managed.provider_id = row.provider_id
            managed.deployment_id = row.id
            managed.node = node
            managed.vm_id = vm_id
            managed.name = row.name
            managed.management_mode = 'proxmox'
            managed.lifecycle_status = 'active'
            managed.destroyed_at = None

        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == row.id,
        ))
        blueprint_variables = dict((((row.workflow or {}).get('blueprint') or {}).get('variables') or {}))
        raw_tags = variables.get('tags') or []
        tags = list(raw_tags) if isinstance(raw_tags, list) else [
            item for item in str(raw_tags).replace(',', ';').split(';') if item
        ]
        metadata = {
            key: value for key, value in {
                'node': node,
                'vm_id': vm_id,
                'management_mode': 'proxmox',
                'apmid': blueprint_variables.get('apmid'),
                'environment': blueprint_variables.get('environment'),
                'organization': blueprint_variables.get('organization'),
                'project': blueprint_variables.get('project'),
                'resource_scope_key': blueprint_variables.get('scope_key'),
                'tags': tags,
            }.items()
            if value not in (None, '', [])
        }
        if resource is None:
            resource = ManagedResource(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                deployment_id=row.id,
                provider_id=row.provider_id,
                provider='proxmox',
                resource_type='vm',
                external_id=str(vm_id),
                name=row.name,
                primary_ip=primary_ip,
                lifecycle_status='active',
                metadata_json=metadata,
                created_by=row.created_by,
            )
            db.add(resource)
        else:
            resource.tenant_id = row.tenant_id
            resource.project_id = row.project_id
            resource.provider_id = row.provider_id
            resource.provider = 'proxmox'
            resource.resource_type = 'vm'
            resource.external_id = str(vm_id)
            resource.name = row.name
            if primary_ip:
                resource.primary_ip = primary_ip
            resource.lifecycle_status = 'active'
            resource.metadata_json = metadata
            resource.destroyed_at = None
        db.commit()

    _persist(context, inventory_synced=True)
    context.log(f'inventory.vm.registered: {node} / VMID {vm_id} / management=proxmox')
    return {'node': node, 'vm_id': vm_id}


def destroy(context, timeout=1800):
    node, vm_id, adapter = identity(context)
    force = (context.job.payload or {}).get('force') is True
    context.stage('proxmox.destroy.check')
    try:
        status = _status_or_none(adapter, node, vm_id)
    except Exception as exc:
        if not force:
            raise ExecutionFailed('Nie udało się sprawdzić VM przed usunięciem') from None
        status = {}
        context.log(
            f'proxmox.destroy.force_precheck_failed: {node}/{vm_id}; '
            f'{type(exc).__name__}: {str(exc)[:200]}'
        )
    if status is None:
        context.log(f'proxmox.destroy.absent: {node}/{vm_id}')
        return True

    if force or str(status.get('status') or '').lower() != 'stopped':
        context.stage('proxmox.destroy.force_stop')
        context.progress(None, 'Twarde zatrzymywanie VM', phase='destroy')
        try:
            task = adapter.vm_power(node, vm_id, 'stop')
            if task:
                wait_task(
                    context, adapter, task_node(task, node), task, 'Twarde zatrzymywanie VM',
                    phase='destroy', timeout=min(timeout, 300),
                )
        except Exception as exc:
            if not force:
                raise
            context.log(
                f'proxmox.destroy.force_stop_failed: {node}/{vm_id}; '
                f'{type(exc).__name__}: {str(exc)[:200]}'
            )

    context.stage('proxmox.destroy.delete')
    context.progress(None, 'Usuwanie VM przez Proxmox API', phase='destroy')
    try:
        task = adapter.delete_vm(
            node,
            vm_id,
            purge=True,
            destroy_unreferenced_disks=False,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return True
        detail = exc.detail if isinstance(exc.detail, str) else f'HTTP {exc.status_code}'
        raise ExecutionFailed('Usuwanie VM w Proxmox nie powiodło się: ' + detail[:300]) from None
    context.quota_provider_submitted = True
    if task:
        wait_task(
            context, adapter, task_node(task, node), task, 'Usuwanie VM',
            phase='destroy', timeout=timeout,
        )
    context.progress(100, 'VM usunięta', phase='destroy')
    return True
