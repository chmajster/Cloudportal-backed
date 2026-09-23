import base64
import io
import json
import re
import shlex

import paramiko

from app.credentials.ssh import generate_ed25519_key_pair, public_key_from_private_key
from app.executors.base import ExecutionFailed
from app.models import Credential, now
from app.security.core import decrypt_blob, decrypt_secret, encrypt_blob


BOOTSTRAP_PREFIX = 'cloudportal-bootstrap'


def create_guest_bootstrap_secret(deployment_name: str) -> tuple[dict, str]:
    private_key, public_key = generate_ed25519_key_pair()
    safe = re.sub(r'[^a-z0-9]+', '', str(deployment_name or '').lower())[:8]
    username = f'{BOOTSTRAP_PREFIX}-{safe}' if safe else BOOTSTRAP_PREFIX
    payload = json.dumps({'username': username, 'private_key': private_key}).encode()
    encrypted = encrypt_blob(payload, 'guest-bootstrap')
    return {'ssh_username': username, 'ssh_public_key': public_key}, base64.b64encode(encrypted).decode()


def decrypt_guest_bootstrap_secret(value: str) -> dict:
    try:
        encrypted = base64.b64decode(str(value or ''), validate=True)
        payload = json.loads(decrypt_blob(encrypted, 'guest-bootstrap').decode())
    except Exception:
        raise ExecutionFailed('Guest bootstrap secret is unavailable or invalid') from None
    if not payload.get('username') or not payload.get('private_key'):
        raise ExecutionFailed('Guest bootstrap secret is incomplete')
    return payload


def _run(client, command, *, stdin_text=None, sensitive=()):
    stdin, stdout, stderr = client.exec_command(command, timeout=120)
    if stdin_text is not None:
        stdin.write(stdin_text)
        stdin.flush()
        stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    output = (stdout.read() + stderr.read()).decode('utf-8', errors='replace').strip()
    for value in sensitive:
        if value:
            output = output.replace(str(value), '***')
    if code:
        raise ExecutionFailed(f'Guest bootstrap command failed ({code}): {output[:500]}')
    return output


def _target_credential(db, credential_id):
    credential = db.get(Credential, int(credential_id))
    if credential is None or credential.type != 'ssh':
        raise ExecutionFailed('Guest SSH credential is missing or invalid')
    if credential.expires_at is not None and credential.expires_at <= now():
        raise ExecutionFailed('Guest SSH credential expired before guest bootstrap')
    if not credential.username:
        raise ExecutionFailed('Guest SSH credential must define a username')
    secret = decrypt_secret(credential)
    if not secret.get('password') and not secret.get('private_key'):
        raise ExecutionFailed('Guest SSH credential has no password or private key')
    return credential, secret


def provision_guest(context, address: str, bootstrap: dict, db, credential_id: int, install_qemu_agent: bool):
    credential, secret = _target_credential(db, credential_id)
    try:
        key = paramiko.Ed25519Key.from_private_key(io.StringIO(bootstrap['private_key']))
    except (paramiko.SSHException, ValueError):
        raise ExecutionFailed('Guest bootstrap private key is invalid') from None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=address,
            port=22,
            username=bootstrap['username'],
            pkey=key,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        username = credential.username
        quoted_user = shlex.quote(username)
        _run(
            client,
            f"sudo -n sh -c 'id -u {quoted_user} >/dev/null 2>&1 || useradd -m -s /bin/bash {quoted_user}'",
        )
        _run(
            client,
            f"sudo -n sh -c 'install -d -m 700 -o {quoted_user} -g {quoted_user} /home/{quoted_user}/.ssh'",
        )
        private_key = secret.get('private_key')
        if private_key:
            public_key = public_key_from_private_key(private_key)
            quoted_key = shlex.quote(public_key)
            _run(
                client,
                f"sudo -n sh -c 'printf %s\\n {quoted_key} > /home/{quoted_user}/.ssh/authorized_keys && "
                f"chown {quoted_user}:{quoted_user} /home/{quoted_user}/.ssh/authorized_keys && "
                f"chmod 600 /home/{quoted_user}/.ssh/authorized_keys'",
            )
        password = secret.get('password')
        if password:
            _run(
                client,
                "sudo -n chpasswd",
                stdin_text=f'{username}:{password}\n',
                sensitive=(password,),
            )
        if install_qemu_agent:
            _run(
                client,
                "sudo -n sh -c '"
                "if command -v apt-get >/dev/null 2>&1; then apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y qemu-guest-agent; "
                "elif command -v dnf >/dev/null 2>&1; then dnf install -y qemu-guest-agent; "
                "elif command -v yum >/dev/null 2>&1; then yum install -y qemu-guest-agent; "
                "elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install qemu-guest-agent; "
                "else exit 127; fi; "
                "systemctl enable --now qemu-guest-agent'",
            )
        _run(
            client,
            f"sudo -n sh -c 'userdel -r {shlex.quote(bootstrap['username'])} >/dev/null 2>&1 || true'",
        )
    except paramiko.AuthenticationException:
        raise ExecutionFailed('Guest bootstrap SSH authentication failed') from None
    except (OSError, paramiko.SSHException) as exc:
        raise ExecutionFailed(f'Guest bootstrap SSH failed: {exc.__class__.__name__}') from None
    finally:
        client.close()
