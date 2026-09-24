"""First-boot NoCloud media, uploaded by Terraform's HTTP ISO resource.

Only reference IDs are stored in a Blueprint. Neither cleartext passwords nor
private keys are passed to Terraform; the seed contains a salted password hash.
Call preparation while holding the deployment and workspace locks.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

import yaml

from app.executors.base import ExecutionFailed


MANIFEST = '.cloudportal-cloud-init.json'
USERNAME = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
MAC = re.compile(r'^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$', re.I)
DRIVE = re.compile(r'^(?:ide[0-3]|sata[0-5]|scsi(?:[0-9]|[12][0-9]|30))$')
SCHEMA_VERSION = 1
AGENT_START = '''set -eu
if command -v systemctl >/dev/null 2>&1; then
  case "$(systemctl is-enabled qemu-guest-agent 2>/dev/null || true)" in
    static|indirect|enabled|enabled-runtime) ;;
    *) systemctl enable qemu-guest-agent ;;
  esac
  systemctl start qemu-guest-agent
  systemctl is-active --quiet qemu-guest-agent
elif command -v rc-update >/dev/null 2>&1; then
  rc-update add qemu-guest-agent default
  rc-service qemu-guest-agent start
  rc-service qemu-guest-agent status
else
  echo "No supported service manager for qemu-guest-agent" >&2
  exit 1
fi
'''


def blueprint_snapshot(context):
    return ((getattr(context.job, 'payload', None) or {}).get('blueprint')
            or ((getattr(context.deployment, 'workflow', None) or {}).get('blueprint') or {}))


def native_cloud_init_requested(context):
    steps = blueprint_snapshot(context).get('steps') or []
    seeds = [step for step in steps if step.get('type') == 'cloud_init']
    if not seeds:
        return False
    # Preserve old implicit/declarative workflows. New workflows have an
    # explicit apply and opt in with a cloud_init predecessor.
    applies = [step for step in steps if step.get('type') == 'terraform_apply']
    if not applies:
        return False
    if context.deployment.provider != 'proxmox' or context.deployment.template != 'proxmox-vm':
        raise ExecutionFailed('Native Cloud-init requires the bundled proxmox-vm template')
    if len(seeds) != 1 or seeds[0].get('conditions') or seeds[0].get('retry') or seeds[0].get('rollback'):
        raise ExecutionFailed('Cloud-init requires one unconditional configuration step without rollback')
    by_id = {step['id']: step for step in steps}
    seed_id = seeds[0]['id']

    def ancestors(step_id):
        result = set()
        pending = list(by_id[step_id].get('depends_on') or [])
        while pending:
            parent = pending.pop()
            if parent == step_id or parent not in by_id:
                raise ExecutionFailed('Invalid Cloud-init workflow dependency')
            if parent in result:
                continue
            result.add(parent)
            pending.extend(by_id[parent].get('depends_on') or [])
        return result

    for step in steps:
        if step.get('type') in {'terraform_plan', 'terraform_apply'} and seed_id not in ancestors(step['id']):
            raise ExecutionFailed('Cloud-init must precede every Terraform plan/apply step')
    return True


def hash_password(password, salt):
    if not password:
        return None
    if not isinstance(password, str) or any(char in password for char in '\r\n\x00'):
        raise ExecutionFailed('Cloud-init password must not contain line breaks or NUL')
    try:
        result = subprocess.run(
            ['openssl', 'passwd', '-6', '-salt', 'rounds=100000$' + salt, '-stdin'],
            input=password + '\n', text=True, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ExecutionFailed('Cloud-init password hashing requires a working openssl executable') from None
    hashed = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'\$6\$rounds=100000\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}', hashed):
        # Never include subprocess output: a failed hashing utility can echo input.
        raise ExecutionFailed('Cloud-init password hashing failed')
    return hashed


def render_seed(variables, *, instance_id, mac_address, password_hash=None,
                guest_account_mode='cloud_init_managed'):
    if guest_account_mode not in {'cloud_init_managed', 'existing_template'}:
        raise ExecutionFailed('Unsupported Cloud-init guest account mode')
    if not MAC.fullmatch(mac_address):
        raise ExecutionFailed('Cloud-init requires a valid primary NIC MAC address')
    config = {
        'hostname': variables['name'],
        'manage_etc_hosts': True,
    }
    if guest_account_mode == 'cloud_init_managed':
        username = str(variables.get('ssh_username') or 'clouduser')
        if not USERNAME.fullmatch(username):
            raise ExecutionFailed('Cloud-init requires a valid Linux username (1-32 lowercase characters)')
        public_key = variables.get('ssh_public_key')
        if public_key and ('\n' in public_key or not public_key.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-'))):
            raise ExecutionFailed('Cloud-init requires one SSH public key')
        user = {'name': username, 'shell': '/bin/sh', 'lock_passwd': not bool(password_hash)}
        if username != 'root':
            user['sudo'] = 'ALL=(ALL) NOPASSWD:ALL'
        if public_key:
            user['ssh_authorized_keys'] = [public_key]
        if password_hash:
            user['hashed_passwd'] = password_hash
        config.update({
            'users': [user],
            'ssh_pwauth': bool(password_hash),
            'chpasswd': {'expire': False},
            'disable_root': username != 'root',
        })
    if variables.get('install_qemu_guest_agent'):
        config.update(package_update=True, packages=['qemu-guest-agent'], runcmd=[['sh', '-ec', AGENT_START]])
    nic = {'match': {'macaddress': mac_address.lower()}, 'dhcp4': not bool(variables.get('ipv4_address')), 'dhcp6': False}
    if variables.get('ipv4_address'):
        nic['addresses'] = [variables['ipv4_address']]
        if variables.get('ipv4_gateway'):
            nic['routes'] = [{'to': '0.0.0.0/0', 'via': variables['ipv4_gateway']}]
    dns = {}
    if variables.get('dns_servers'):
        dns['addresses'] = list(variables['dns_servers'])
    if variables.get('dns_domain'):
        dns['search'] = [variables['dns_domain']]
    if dns:
        nic['nameservers'] = dns
    return {
        'user-data': '#cloud-config\n' + yaml.safe_dump(config, sort_keys=False),
        'meta-data': yaml.safe_dump({'instance-id': instance_id, 'local-hostname': variables['name']}, sort_keys=False),
        'network-config': yaml.safe_dump({'version': 2, 'ethernets': {'primary': nic}}, sort_keys=False),
    }


def choose_iso_storage(rows, preferred):
    available = []
    for row in rows:
        content = row.get('content') or ''
        content = content if isinstance(content, list) else str(content).split(',')
        if 'iso' not in [str(item).strip() for item in content]:
            continue
        if str(row.get('disable', '0')).lower() in {'1', 'true'}:
            continue
        if any(str(row.get(key, '1')).lower() in {'0', 'false'} for key in ('active', 'enabled')):
            continue
        name = str(row.get('storage') or row.get('id') or '')
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', name):
            available.append(name)
    for name in (preferred, 'local', *sorted(available)):
        if name in available:
            return name
    raise ExecutionFailed('Cloud-init requires active ISO-capable storage on the target Proxmox node; enable ISO content on a storage such as local')


def seed_interface(config):
    if str(config.get('ostype') or '').lower().startswith(('win', 'w2k', 'wxp', 'wvista')):
        raise ExecutionFailed('This Cloud-init workflow supports Linux cloud images, not Windows/Cloudbase-init')
    existing = [key for key, value in config.items() if DRIVE.fullmatch(key) and 'cloudinit' in str(value).lower()]
    if len(existing) > 1:
        raise ExecutionFailed('Template has multiple Cloud-init drives; retain exactly one before cloning')
    if existing:
        return existing[0]
    # Only an unused or explicitly empty CD-ROM slot can be replaced. Never
    # overwrite an arbitrary inherited ISO or disk.
    for slot in ['ide2', 'ide0', *(f'sata{index}' for index in range(6))]:
        value = str(config.get(slot) or '')
        if not value or (value.startswith('none,') and 'media=cdrom' in value):
            return slot
    raise ExecutionFailed('Template has no available CD-ROM interface for the Cloud-init seed')


def _read_json(path, label):
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raise ExecutionFailed(label + ' is missing or invalid') from None
    if not isinstance(result, dict):
        raise ExecutionFailed(label + ' is invalid')
    return result


def _private_write(path, content):
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_iso(path, files):
    try:
        import pycdlib
    except ImportError:
        raise ExecutionFailed('Cloud-init ISO support is missing; install the updated worker dependencies (pycdlib)') from None
    iso = pycdlib.PyCdlib()
    temporary = path.with_name(path.name + '.tmp')
    streams = []
    try:
        iso.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='CIDATA')
        for index, (name, content) in enumerate(files.items()):
            data = content.encode('utf-8')
            stream = io.BytesIO(data)
            streams.append(stream)
            iso.add_fp(stream, len(data), iso_path=f'/SEED{index}.;1', rr_name=name,
                       joliet_path='/' + name, file_mode=0o100600)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as target:
            iso.write_fp(target)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        iso.close()
        for stream in streams:
            stream.close()
        temporary.unlink(missing_ok=True)


def prepare_native_seed(context, workspace, guest_variables, guest_password):
    """Prepare or verify immutable seed input before Terraform runs.

    An approved saved plan must reuse the exact previously generated ISO. Missing
    workspace media never causes a new plan, password rotation, or guest rebuild.
    """
    from app.providers.proxmox import ProxmoxProvider

    context.check()
    workspace = Path(workspace)
    deployment = context.deployment
    manifest_path = workspace / MANIFEST
    saved_apply = bool(getattr(context, 'apply_saved_terraform_plan', False))
    manifest = _read_json(manifest_path, 'Cloud-init media manifest') if manifest_path.exists() else {}
    if saved_apply and not manifest:
        raise ExecutionFailed('Approved plan has no Cloud-init media; generate and approve a new plan')
    if manifest and (manifest.get('version') != SCHEMA_VERSION or manifest.get('deployment_id') != deployment.id):
        raise ExecutionFailed('Cloud-init media belongs to a different deployment or unsupported version')
    if manifest:
        binding = manifest.get('binding')
        if (not isinstance(binding, dict) or not {'node', 'storage', 'interface', 'mac'} <= set(binding)
                or not DRIVE.fullmatch(str(binding.get('interface', '')))
                or not MAC.fullmatch(str(binding.get('mac', '')))
                or not re.fullmatch(r'[A-Za-z0-9./]{1,16}', str(manifest.get('salt', '')))
                or not isinstance(manifest.get('generation'), str)):
            raise ExecutionFailed('Cloud-init media manifest is invalid')
    state_path = workspace / 'terraform.tfstate'
    state = _read_json(state_path, 'Terraform state') if state_path.exists() else {}
    resources = state.get('resources') or []
    vms = [row for row in resources if row.get('type') == 'proxmox_virtual_environment_vm' and row.get('name') == 'vm' and row.get('instances')]
    seeds = [row for row in resources if row.get('type') == 'proxmox_virtual_environment_file' and row.get('name') == 'cloud_init_seed' and row.get('instances')]
    recreate = bool((context.job.payload or {}).get('_recreate'))
    if vms and not seeds and not recreate:
        raise ExecutionFailed('Cloud-init cannot be retrofitted to an existing VM automatically; use an explicitly approved rebuild or guest day-2 configuration')
    if vms and not manifest and not recreate:
        raise ExecutionFailed('Existing VM Cloud-init media is missing; restore the deployment workspace before retrying')
    generation = manifest.get('generation') or deployment.id
    if recreate and not saved_apply:
        generation = context.job.id
    salt = manifest.get('salt') or secrets.token_hex(8)
    variables = dict(deployment.variables or {})
    variables.update(guest_variables)
    guest_account_mode = str(
        blueprint_snapshot(context).get('guest_account_mode') or 'cloud_init_managed'
    )
    hashed = (
        hash_password(guest_password, salt)
        if guest_account_mode == 'cloud_init_managed'
        else None
    )
    if saved_apply:
        binding = manifest['binding']
    else:
        provider = ProxmoxProvider(context.credential)
        try:
            storage = choose_iso_storage(provider.discover('storages', variables['node']) or [], variables.get('storage'))
            template_config = provider.vm_config(variables.get('template_node') or variables['node'], variables['template_id']) or {}
        except ExecutionFailed:
            raise
        except Exception:
            raise ExecutionFailed('Cloud-init preflight cannot read ISO storage or source VM configuration; check Proxmox API permissions') from None
        interface = seed_interface(template_config)
        mac = '02:' + ':'.join(hashlib.sha256(deployment.id.encode()).hexdigest()[offset:offset + 2] for offset in range(0, 10, 2))
        if vms and manifest:
            interface = manifest['binding']['interface']
            mac = manifest['binding']['mac']
        binding = {'node': variables['node'], 'storage': storage, 'interface': interface, 'mac': mac}
    files = render_seed(
        variables,
        instance_id='cloudportal-' + generation,
        mac_address=binding['mac'],
        password_hash=hashed,
        guest_account_mode=guest_account_mode,
    )
    fingerprint = hashlib.sha256(json.dumps({'files': files, 'binding': binding}, sort_keys=True).encode()).hexdigest()
    if saved_apply or (vms and not recreate):
        if manifest.get('fingerprint') != fingerprint:
            raise ExecutionFailed('Cloud-init configuration or guest credential changed; a new approved plan/rebuild is required')
    filename = 'cloudportal-' + re.sub(r'[^a-zA-Z0-9-]', '', deployment.id)[:40] + '-' + fingerprint[:20] + '.iso'
    path = workspace / filename
    if manifest.get('fingerprint') == fingerprint:
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get('checksum'):
            raise ExecutionFailed('Cloud-init ISO is missing or changed; restore the original deployment media before applying the saved plan')
        if manifest.get('path') != str(path):
            raise ExecutionFailed('Cloud-init workspace path changed; restore the original workspace or generate a new plan')
    else:
        write_iso(path, files)
        manifest = {
            'version': SCHEMA_VERSION, 'deployment_id': deployment.id,
            'generation': generation, 'salt': salt, 'fingerprint': fingerprint,
            'checksum': hashlib.sha256(path.read_bytes()).hexdigest(),
            'path': str(path), 'binding': binding,
        }
        _private_write(manifest_path, json.dumps(manifest, sort_keys=True))
    context.check()
    context.log('cloud-init.media.ready: NoCloud ISO; upload through Proxmox API; no host/guest SSH required')
    if guest_account_mode == 'existing_template':
        context.log('cloud-init.guest-account: preserve existing template account; use selected credential for later guest access')
    if variables.get('install_qemu_guest_agent'):
        context.log('cloud-init.qemu-agent: install, enable and start during first guest boot')
    return {
        'cloud_init_seed_path': str(path),
        'cloud_init_seed_checksum': manifest['checksum'],
        'cloud_init_seed_storage': binding['storage'],
        'cloud_init_seed_interface': binding['interface'],
        'cloud_init_seed_mac': binding['mac'],
        'qemu_guest_agent_bootstrap': False,
    }
