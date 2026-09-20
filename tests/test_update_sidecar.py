import importlib.util
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


def test_update_sidecar_recovers_complete_state_as_success(tmp_path):
    config = tmp_path / 'etc'
    data = tmp_path / 'data'
    app_root = tmp_path / 'app'
    current = app_root / 'current'
    config.mkdir()
    data.mkdir()
    current.mkdir(parents=True)

    control = 'control-' + 'c' * 48
    status = 'status-' + 'd' * 48
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
        'commit_sha': '2' * 40,
        'ref': 'main',
    }))
    state_dir = data / 'update'
    state_dir.mkdir(parents=True)
    (state_dir / 'state.json').write_text(json.dumps({
        'status': 'running',
        'phase': 'complete',
        'progress': 100,
        'message': 'Instalacja lub aktualizacja zakończona pomyślnie.',
        'current_version': '222222222222',
        'target_version': '222222222222',
        'update_available': False,
        'events': [],
        'output': [],
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

        code, recovered = request(base + '/status', status_token=status)
        assert code == 200
        assert recovered['status'] == 'success'
        assert recovered['phase'] == 'complete'
        assert recovered['progress'] == 100
        assert recovered['update_available'] is False
        assert recovered['finished_at']
        assert recovered['message'] == 'Aktualizacja zakończona pomyślnie.'
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def load_update_service_module(tmp_path, monkeypatch):
    config = tmp_path / 'module-etc'
    data = tmp_path / 'module-data'
    app_root = tmp_path / 'module-app'
    config.mkdir()
    data.mkdir()
    app_root.mkdir()
    monkeypatch.setenv('CP_UPDATER_CONFIG_DIR', str(config))
    monkeypatch.setenv('CP_UPDATER_DATA_DIR', str(data))
    monkeypatch.setenv('CP_UPDATER_APP_ROOT', str(app_root))
    spec = importlib.util.spec_from_file_location(
        'cloudportal_update_service_test',
        ROOT / 'scripts' / 'update-service.py',
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_state_unlocks_orphaned_running_state(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.atomic_json(updater.STATE_FILE, {
        **updater.default_state(),
        'status': 'running',
        'phase': 'backup',
        'progress': 10,
        'message': 'Tworzenie backupu.',
        'started_at': updater.utcnow(),
    })
    updater.update_thread = None

    state = updater.runtime_state()

    assert state['operation_active'] is False
    assert state['status'] == 'failed'
    assert state['phase'] == 'interrupted'
    assert 'nie jest już aktywny' in state['message']
    assert state['finished_at']


def test_run_update_ignores_stale_persisted_running_flag(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.atomic_json(updater.STATE_FILE, {
        **updater.default_state(),
        'status': 'running',
        'phase': 'preflight',
        'progress': 1,
        'message': 'Stary stan.',
    })
    calls = []

    def fake_check(ref, update_context=False):
        calls.append((ref, update_context))
        updater.save_state(
            status='up_to_date',
            phase='up_to_date',
            progress=100,
            message='Aktualny.',
            update_available=False,
            finished_at=updater.utcnow(),
        )
        return {'update_available': False}

    monkeypatch.setattr(updater, 'check_remote', fake_check)
    updater.run_update('main', automatic=False)

    assert calls == [('main', True)]
    state = updater.load_state()
    assert state['status'] == 'up_to_date'
    assert state['phase'] == 'up_to_date'
