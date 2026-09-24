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



def test_required_ci_status_is_fail_closed(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    target = 'a' * 40
    settings = updater.default_settings()

    monkeypatch.setattr(updater, '_http_json_ci', lambda url, cfg: {
        'workflow_runs': [{
            'head_sha': target,
            'name': 'Backend CI',
            'status': 'completed',
            'conclusion': 'failure',
            'html_url': 'https://example.invalid/run/1',
            'updated_at': '2026-09-22T08:00:00Z',
            'run_attempt': 1,
        }]
    })
    failed = updater.required_ci_status(target, settings)
    assert failed['state'] == 'failed'
    assert failed['workflow'] == 'Backend CI'

    monkeypatch.setattr(updater, '_http_json_ci', lambda url, cfg: {'workflow_runs': []})
    pending = updater.required_ci_status(target, settings)
    assert pending['state'] == 'pending'

    monkeypatch.setattr(updater, '_http_json_ci', lambda url, cfg: {
        'workflow_runs': [{
            'head_sha': target,
            'name': 'Backend CI',
            'status': 'completed',
            'conclusion': 'success',
            'html_url': 'https://example.invalid/run/2',
            'updated_at': '2026-09-22T08:05:00Z',
            'run_attempt': 1,
        }]
    })
    passed = updater.required_ci_status(target, settings)
    assert passed['state'] == 'success'


def test_run_update_gates_before_backup_and_pins_verified_sha(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    target = 'b' * 40
    calls = []
    captured = {}

    monkeypatch.setattr(updater, 'check_remote', lambda ref, update_context=False: {
        'update_available': True,
        'target_sha': target,
        'target_version': target[:12],
        'target_commit_at': '2026-09-22T08:10:00Z',
    })
    monkeypatch.setattr(updater, 'wait_for_required_ci', lambda sha, settings: calls.append(('ci', sha)))
    monkeypatch.setattr(updater, 'validate_candidate', lambda sha, settings: calls.append(('candidate', sha)))
    backup_dir = tmp_path / 'backup'
    backup_dir.mkdir()
    (backup_dir / 'database.dump').write_bytes(b'dump')
    (backup_dir / 'metadata.json').write_text('{}')
    monkeypatch.setattr(updater, 'pre_update_backup', lambda: (calls.append(('backup', None)), backup_dir)[1])
    monkeypatch.setattr(
        updater,
        'validate_candidate_runtime',
        lambda sha, backup, settings: calls.append(('runtime', sha, backup)),
    )

    def fake_download(ref, path):
        calls.append(('download', ref))
        path.write_text('#!/usr/bin/env bash\nexit 0\n')

    def fake_args(ref):
        calls.append(('args', ref))
        return ['/bin/true']

    class FakeProcess:
        def __init__(self):
            self.stdout = iter(['::cloudportal-progress::100::complete::done\n'])

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        captured['args'] = args
        captured['env'] = kwargs['env']
        return FakeProcess()

    monkeypatch.setattr(updater, 'download_installer', fake_download)
    monkeypatch.setattr(updater, 'installer_args', fake_args)
    monkeypatch.setattr(updater.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(updater, 'verify_installed_release', lambda sha: calls.append(('verify', sha)))

    updater.run_update('main', automatic=False)

    assert calls[:3] == [('ci', target), ('candidate', target), ('backup', None)]
    assert ('runtime', target, backup_dir) in calls
    assert ('download', target) in calls
    assert ('args', target) in calls
    assert ('verify', target) in calls
    assert captured['env']['CLOUDPORTAL_RELEASE_SHA'] == target
    assert captured['env']['CLOUDPORTAL_UPDATE_CHANNEL_REF'] == 'main'
    state = updater.load_state()
    assert state['status'] == 'success'
    assert state['current_version'] == target[:12]


def test_run_update_does_not_touch_backup_when_ci_fails(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    target = 'c' * 40
    touched = []

    monkeypatch.setattr(updater, 'check_remote', lambda ref, update_context=False: {
        'update_available': True,
        'target_sha': target,
        'target_version': target[:12],
        'target_commit_at': '2026-09-22T08:15:00Z',
    })
    monkeypatch.setattr(
        updater,
        'wait_for_required_ci',
        lambda sha, settings: (_ for _ in ()).throw(RuntimeError('CI failed')),
    )
    monkeypatch.setattr(updater, 'pre_update_backup', lambda: touched.append('backup'))
    monkeypatch.setattr(updater, 'download_installer', lambda ref, path: touched.append('download'))

    updater.run_update('main', automatic=False)

    assert touched == []
    state = updater.load_state()
    assert state['status'] == 'failed'
    assert state['phase'] == 'failed'
    assert 'CI failed' in state['message']



def test_runtime_preflight_url_helpers_support_installer_local_services(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    database = 'postgresql+psycopg:///cloudportal?host=/var/run/postgresql'
    details = updater._database_clone_details(database)
    assert details['host'] == '/var/run/postgresql'
    assert details['username'] == 'cloudportal'
    assert details['database'] == 'cloudportal'
    assert updater._url_with_database(database, 'cloudportal_preflight_1').startswith(
        'postgresql+psycopg:///cloudportal_preflight_1?'
    )
    assert updater._scratch_redis_url('redis://:secret@127.0.0.1:6389/0').endswith('/15')
    assert updater._scratch_redis_url('redis://:secret@127.0.0.1:6389/15').endswith('/14')


def test_docker_runtime_preflight_runs_isolated_validation(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    backup = tmp_path / 'backup'
    backup.mkdir()
    (backup / 'database.dump').write_bytes(b'dump')
    calls = []

    monkeypatch.setattr(updater, 'INSTALL_MODE', 'docker')
    monkeypatch.setattr(
        updater,
        '_docker_validate_candidate_runtime',
        lambda sha, backup_dir, settings: calls.append((sha, backup_dir, settings['runtime_preflight'])),
    )

    updater.validate_candidate_runtime('d' * 40, backup, updater.default_settings())

    assert calls == [('d' * 40, backup, True)]


def test_docker_runtime_preflight_can_be_explicitly_disabled(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    backup = tmp_path / 'backup'
    backup.mkdir()
    called = []

    monkeypatch.setattr(updater, 'INSTALL_MODE', 'docker')
    monkeypatch.setattr(
        updater,
        '_docker_validate_candidate_runtime',
        lambda *args: called.append(args),
    )
    settings = updater.default_settings()
    settings['runtime_preflight'] = False

    updater.validate_candidate_runtime('e' * 40, backup, settings)

    assert called == []


def test_runtime_preflight_rejects_external_database_without_mutating_it(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    try:
        updater._database_clone_details(
            'postgresql+psycopg://cloudportal:secret@db.example.com:5432/cloudportal'
        )
    except RuntimeError as exc:
        assert 'lokalnego PostgreSQL' in str(exc)
    else:
        raise AssertionError('external PostgreSQL must fail closed')


def test_candidate_core_health_ignores_workers_but_requires_runtime_dependencies(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    payload = {
        'status': 'degraded',
        'checks': {
            'api': True,
            'database': True,
            'queue': True,
            'dispatcher': False,
            'workers': {'online': 0, 'expected': 1},
            'terraform': True,
            'ansible': True,
            'disk': True,
            'encryption': True,
        },
    }
    assert updater._candidate_core_healthy(payload) is True
    payload['checks']['database'] = False
    assert updater._candidate_core_healthy(payload) is False


def test_pre_update_backup_requires_verifiable_snapshot(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    backup_root = tmp_path / 'backups' / '20260922T100000Z'
    backup_root.mkdir(parents=True)
    (backup_root / 'database.dump').write_bytes(b'dump')
    (backup_root / 'metadata.json').write_text('{}')
    monkeypatch.setattr(updater.Path, 'exists', lambda self: True)

    class Result:
        returncode = 0
        stdout = str(backup_root) + '\n'

    monkeypatch.setattr(updater.subprocess, 'run', lambda *args, **kwargs: Result())
    assert updater.pre_update_backup() == backup_root
