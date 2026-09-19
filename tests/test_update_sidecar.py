import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(url, *, token=None, status_token=None, method='GET', payload=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['X-Updater-Token'] = token
    if status_token:
        headers['X-Update-Status-Token'] = status_token
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=2) as response:
        return response.status, json.loads(response.read().decode())


def test_update_sidecar_remains_independent_and_protects_control_plane(tmp_path):
    config = tmp_path / 'etc'
    data = tmp_path / 'data'
    app_root = tmp_path / 'app'
    current = app_root / 'current'
    config.mkdir()
    data.mkdir()
    current.mkdir(parents=True)

    control = 'control-' + 'a' * 48
    status = 'status-' + 'b' * 48
    (config / 'updater.token').write_text(control)
    (config / 'updater-status.token').write_text(status)
    (config / 'updater.json').write_text(json.dumps({
        'enabled': False,
        'interval_hours': 24,
        'ref': 'main',
        'github_token_file': '',
        'github_config': '',
    }))
    (current / '.cloudportal-release.json').write_text(json.dumps({
        'commit_sha': '1' * 40,
        'ref': 'main',
    }))

    port = free_port()
    env = {
        **os.environ,
        'CP_UPDATER_CONFIG_DIR': str(config),
        'CP_UPDATER_DATA_DIR': str(data),
        'CP_UPDATER_APP_ROOT': str(app_root),
        'CP_UPDATER_PORT': str(port),
    }
    process = subprocess.Popen(
        [sys.executable, str(ROOT / 'scripts' / 'update-service.py')],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                assert request(base + '/health')[0] == 200
                break
            except Exception:
                if time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise AssertionError(f'Updater did not start: {stdout}\n{stderr}')
                time.sleep(0.05)

        try:
            request(base + '/status')
            raise AssertionError('status endpoint accepted an unauthenticated request')
        except HTTPError as exc:
            assert exc.code == 401

        code, state = request(base + '/status', status_token=status)
        assert code == 200
        assert state['current_version'] == '111111111111'
        assert state['status'] == 'idle'

        code, settings = request(base + '/settings', token=control)
        assert code == 200
        assert settings == {'enabled': False, 'interval_hours': 24, 'ref': 'main'}

        code, saved = request(
            base + '/settings',
            token=control,
            method='POST',
            payload={'enabled': True, 'interval_hours': 12, 'ref': 'stable'},
        )
        assert code == 200
        assert saved == {'enabled': True, 'interval_hours': 12, 'ref': 'stable'}
        assert json.loads((config / 'updater.json').read_text())['ref'] == 'stable'
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
