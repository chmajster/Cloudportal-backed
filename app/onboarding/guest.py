import json
import shlex
from urllib.parse import urlsplit

import paramiko
import winrm

from app.security.core import decrypt_secret


class GuestDiscoveryError(RuntimeError):
    pass


def _target_address(resource, credential):
    addresses = list(resource.get('addresses') or [])
    if addresses:
        return addresses[0]
    endpoint = str(credential.endpoint or '').strip()
    if endpoint:
        parsed = urlsplit(endpoint if '://' in endpoint else 'ssh://' + endpoint)
        if parsed.hostname:
            return parsed.hostname
    raise GuestDiscoveryError('Guest address is unavailable')


def _ssh_client(resource, credential):
    secret = decrypt_secret(credential)
    host = _target_address(resource, credential)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if bool(secret.get('accept_unknown_host_key')):
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    options = {
        'hostname': host,
        'username': credential.username,
        'timeout': 10,
        'banner_timeout': 10,
        'auth_timeout': 10,
        'look_for_keys': False,
        'allow_agent': False,
    }
    if secret.get('private_key'):
        from io import StringIO
        key_text = str(secret['private_key'])
        key = None
        for key_type in (
            paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey,
        ):
            try:
                key = key_type.from_private_key(StringIO(key_text), password=secret.get('passphrase'))
                break
            except Exception:
                continue
        if key is None:
            raise GuestDiscoveryError('SSH private key cannot be parsed')
        options['pkey'] = key
    elif secret.get('password'):
        options['password'] = secret['password']
    try:
        client.connect(**options)
    except Exception as exc:
        client.close()
        raise GuestDiscoveryError('SSH guest connection failed: ' + exc.__class__.__name__) from None
    return client


def _ssh_exec(client, command, timeout=20, max_bytes=512_000):
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read(max_bytes + 1)
    err = stderr.read(64_000)
    code = stdout.channel.recv_exit_status()
    if len(out) > max_bytes:
        out = out[:max_bytes]
    return {
        'code': code,
        'stdout': out.decode('utf-8', errors='replace'),
        'stderr': err.decode('utf-8', errors='replace')[:4000],
    }


def _linux_discovery(resource, credential):
    client = _ssh_client(resource, credential)
    try:
        facts = {}
        commands = {
            'hostname': 'hostname -f 2>/dev/null || hostname',
            'os_release': 'cat /etc/os-release 2>/dev/null || true',
            'kernel': 'uname -srmo 2>/dev/null || true',
            'cpu': 'lscpu -J 2>/dev/null || lscpu 2>/dev/null || true',
            'memory': 'cat /proc/meminfo 2>/dev/null || true',
            'disks': 'lsblk -J -b -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS 2>/dev/null || true',
            'addresses': 'ip -j address 2>/dev/null || ip address 2>/dev/null || true',
            'routes': 'ip -j route 2>/dev/null || ip route 2>/dev/null || true',
            'dns': 'cat /etc/resolv.conf 2>/dev/null || true',
            'uptime': 'cat /proc/uptime 2>/dev/null || true',
            'packages': '(command -v dpkg-query >/dev/null && dpkg-query -W -f="${Package} ${Version}\\n") || (command -v rpm >/dev/null && rpm -qa) || true',
            'services': 'systemctl list-units --type=service --all --no-pager --plain 2>/dev/null || true',
            'agent_state': 'systemctl is-active qemu-guest-agent 2>/dev/null || true',
        }
        for key, command in commands.items():
            result = _ssh_exec(client, command, timeout=30 if key in {'packages', 'services'} else 15)
            facts[key] = result['stdout'].strip()
        for key in ('cpu', 'disks', 'addresses', 'routes'):
            try:
                facts[key] = json.loads(facts[key])
            except (TypeError, json.JSONDecodeError):
                pass
        if isinstance(facts.get('packages'), str):
            lines = [line for line in facts['packages'].splitlines() if line.strip()]
            facts['packages'] = lines[:1000]
            facts['packages_truncated'] = len(lines) > 1000
        if isinstance(facts.get('services'), str):
            lines = [line for line in facts['services'].splitlines() if line.strip()]
            facts['services'] = lines[:500]
            facts['services_truncated'] = len(lines) > 500
        return {'transport': 'ssh', 'status': 'complete', **facts}
    finally:
        client.close()


def _winrm_endpoint(resource, credential):
    endpoint = str(credential.endpoint or '').strip()
    if endpoint:
        return endpoint
    address = _target_address(resource, credential)
    return 'https://' + address + ':5986/wsman'


def _windows_discovery(resource, credential):
    secret = decrypt_secret(credential)
    endpoint = _winrm_endpoint(resource, credential)
    transport = str(secret.get('transport') or 'ntlm')
    server_cert_validation = 'validate' if credential.verify_ssl else 'ignore'
    session = winrm.Session(
        endpoint,
        auth=(credential.username, secret.get('password', '')),
        transport=transport,
        server_cert_validation=server_cert_validation,
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
 hostname = $env:COMPUTERNAME
 os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,LastBootUpTime
 cpu = Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors
 memory = Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
 disks = @(Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,DriveType,FileSystem,Size,FreeSpace)
 addresses = @(Get-NetIPAddress | Where-Object {$_.IPAddress -notmatch '^(127\.|::1$|fe80:)'} | Select-Object InterfaceAlias,IPAddress,PrefixLength,AddressFamily)
 dns = @(Get-DnsClientServerAddress | Select-Object InterfaceAlias,AddressFamily,ServerAddresses)
 services = @(Get-Service | Select-Object -First 500 Name,DisplayName,Status,StartType)
 applications = @(
   Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
   Where-Object {$_.DisplayName} | Select-Object -First 1000 DisplayName,DisplayVersion,Publisher
 )
 winrm = (Get-Service WinRM | Select-Object Name,Status,StartType)
}
$result | ConvertTo-Json -Depth 6 -Compress
"""
    try:
        response = session.run_ps(script)
    except Exception as exc:
        raise GuestDiscoveryError('WinRM guest connection failed: ' + exc.__class__.__name__) from None
    if int(response.status_code) != 0:
        raise GuestDiscoveryError('WinRM guest discovery returned a non-zero status')
    try:
        data = json.loads(response.std_out.decode('utf-8', errors='replace'))
    except json.JSONDecodeError:
        raise GuestDiscoveryError('WinRM guest discovery returned invalid JSON') from None
    return {'transport': 'winrm', 'status': 'complete', **data}


def discover_guest(resource, credential):
    if credential.type == 'ssh':
        return _linux_discovery(resource, credential)
    if credential.type == 'winrm':
        return _windows_discovery(resource, credential)
    raise GuestDiscoveryError('Guest discovery requires SSH or WinRM credential')


def install_qemu_guest_agent(resource, credential):
    if credential.type != 'ssh':
        raise GuestDiscoveryError(
            'Direct guest-agent installation is implemented for Linux/SSH. '
            'Use the configured AWX agent installation playbook for Windows.'
        )
    client = _ssh_client(resource, credential)
    try:
        command = (
            "set -eu; "
            "if command -v apt-get >/dev/null; then sudo -n apt-get update && sudo -n apt-get install -y qemu-guest-agent; "
            "elif command -v dnf >/dev/null; then sudo -n dnf install -y qemu-guest-agent; "
            "elif command -v yum >/dev/null; then sudo -n yum install -y qemu-guest-agent; "
            "elif command -v zypper >/dev/null; then sudo -n zypper --non-interactive install qemu-guest-agent; "
            "else echo unsupported-package-manager >&2; exit 42; fi; "
            "sudo -n systemctl enable --now qemu-guest-agent"
        )
        result = _ssh_exec(client, command, timeout=180, max_bytes=128_000)
        if result['code'] != 0:
            raise GuestDiscoveryError(
                'Guest Agent installation failed; passwordless sudo or a supported package manager is required'
            )
        return {'installed': True, 'transport': 'ssh'}
    finally:
        client.close()
