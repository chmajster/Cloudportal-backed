import base64
import hashlib
import io
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException


def parse_ssh_endpoint(endpoint: str):
    parsed = urlsplit(str(endpoint or '').strip())
    if parsed.scheme != 'ssh' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, 'Endpoint SSH musi mieć postać ssh://host:port bez danych logowania.')
    if parsed.path not in {'', '/'}:
        raise HTTPException(422, 'Endpoint SSH nie może zawierać ścieżki.')
    try:
        port = parsed.port or 22
    except ValueError:
        raise HTTPException(422, 'Port SSH jest nieprawidłowy.') from None
    if not 1 <= port <= 65535:
        raise HTTPException(422, 'Port SSH musi być liczbą od 1 do 65535.')
    return parsed.hostname, port


def _fingerprint(key_bytes: bytes) -> str:
    digest = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode('ascii').rstrip('=')
    return 'SHA256:' + digest


def _known_hosts_name(host: str, port: int) -> str:
    return host if port == 22 else f'[{host}]:{port}'


def scan_ssh_host_key(endpoint: str) -> dict:
    host, port = parse_ssh_endpoint(endpoint)
    transport = None
    try:
        transport = paramiko.Transport((host, port))
        transport.banner_timeout = 10
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        return {
            'host': host,
            'port': port,
            'key_type': key.get_name(),
            'fingerprint': _fingerprint(key.asbytes()),
            'known_hosts': f'{_known_hosts_name(host, port)} {key.get_name()} {key.get_base64()}',
        }
    except (OSError, paramiko.SSHException) as exc:
        raise HTTPException(
            502,
            'Nie udało się pobrać klucza hosta SSH. Sprawdź adres, port, routing i usługę sshd.',
        ) from exc
    finally:
        if transport is not None:
            transport.close()


def generate_ed25519_key_pair():
    private = Ed25519PrivateKey.generate()
    private_text = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode('utf-8')
    public_text = private.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode('ascii')
    return private_text, public_text + ' cloudportal'


def _client_with_known_hosts(known_hosts_text: str):
    folder = tempfile.TemporaryDirectory(prefix='cp-ssh-bootstrap-')
    path = Path(folder.name) / 'known_hosts'
    path.write_text(known_hosts_text.strip() + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    client = paramiko.SSHClient()
    client.load_host_keys(str(path))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return folder, client


def _connect(client, endpoint: str, username: str, **auth):
    host, port = parse_ssh_endpoint(endpoint)
    client.connect(
        hostname=host,
        port=port,
        username=username,
        timeout=10,
        auth_timeout=10,
        banner_timeout=10,
        allow_agent=False,
        look_for_keys=False,
        **auth,
    )


def install_generated_key(endpoint: str, username: str, password: str, known_hosts: str) -> dict:
    if not username.strip():
        raise HTTPException(422, 'Podaj użytkownika SSH.')
    if not password:
        raise HTTPException(422, 'Podaj hasło SSH używane do jednorazowej instalacji klucza.')
    if not known_hosts.strip():
        raise HTTPException(422, 'Najpierw pobierz i potwierdź klucz hosta SSH.')

    private_key, public_key = generate_ed25519_key_pair()
    folder = client = None
    try:
        folder, client = _client_with_known_hosts(known_hosts)
        _connect(client, endpoint, username, password=password)
        with client.open_sftp() as sftp:
            home = sftp.normalize('.')
            ssh_dir = home.rstrip('/') + '/.ssh'
            authorized = ssh_dir + '/authorized_keys'
            try:
                sftp.stat(ssh_dir)
            except OSError:
                sftp.mkdir(ssh_dir, mode=0o700)
            sftp.chmod(ssh_dir, 0o700)
            try:
                with sftp.file(authorized, 'r') as handle:
                    raw_existing = handle.read()
                    existing = raw_existing.decode('utf-8', errors='replace') if isinstance(raw_existing, bytes) else str(raw_existing)
            except OSError:
                existing = ''
            key_material = ' '.join(public_key.split()[:2])
            if not any(' '.join(line.split()[:2]) == key_material for line in existing.splitlines() if line.strip()):
                with sftp.file(authorized, 'a') as handle:
                    if existing and not existing.endswith('\n'):
                        handle.write('\n')
                    handle.write(public_key + '\n')
            sftp.chmod(authorized, 0o600)
    except paramiko.BadHostKeyException as exc:
        raise HTTPException(409, 'Klucz hosta SSH zmienił się. Pobierz odcisk ponownie i zweryfikuj serwer.') from exc
    except paramiko.AuthenticationException as exc:
        raise HTTPException(401, 'Logowanie SSH hasłem zostało odrzucone. Sprawdź użytkownika i hasło.') from exc
    except (OSError, paramiko.SSHException) as exc:
        raise HTTPException(502, 'Nie udało się wgrać klucza SSH na serwer.') from exc
    finally:
        if client is not None:
            client.close()
        if folder is not None:
            folder.cleanup()

    verify_folder = verify_client = None
    try:
        key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
        verify_folder, verify_client = _client_with_known_hosts(known_hosts)
        _connect(verify_client, endpoint, username, pkey=key)
    except (OSError, paramiko.SSHException) as exc:
        raise HTTPException(
            502,
            'Klucz został wgrany, ale logowanie wygenerowanym kluczem nie powiodło się. Sprawdź konfigurację sshd i authorized_keys.',
        ) from exc
    finally:
        if verify_client is not None:
            verify_client.close()
        if verify_folder is not None:
            verify_folder.cleanup()

    public_material = ' '.join(public_key.split()[:2])
    raw_public = base64.b64decode(public_material.split()[1])
    return {
        'secret': {'private_key': private_key, 'known_hosts': known_hosts.strip()},
        'public_key': public_key,
        'fingerprint': _fingerprint(raw_public),
    }
