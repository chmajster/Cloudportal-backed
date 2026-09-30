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


def test_ensure_worker_capacity_scales_docker_and_persists_count(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'INSTALL_MODE', 'docker')
    config = updater.CONFIG_DIR / 'docker.env'
    config.write_text(
        'CP_PUBLIC_HOST=cloudportal.example.test\n'
        'CP_HTTPS_PORT=8443\n'
        'CP_WORKER_COUNT=1\n',
        encoding='utf-8',
    )
    calls = []
    monkeypatch.setattr(updater, '_docker_compose_base', lambda: ['docker', 'compose'])
    monkeypatch.setattr(
        updater,
        '_run_worker_command',
        lambda command, label, timeout=300: calls.append((command, label)) or '',
    )
    monkeypatch.setattr(updater, '_schedule_api_refresh_after_worker_change', lambda: None)

    result = updater.ensure_worker_capacity(4)

    assert result == {
        'changed': True,
        'reconciled': True,
        'previous_worker_count': 1,
        'worker_count': 4,
        'install_mode': 'docker',
    }
    assert updater.parse_kv(config)['CP_WORKER_COUNT'] == '4'
    assert calls == [(
        ['docker', 'compose', 'up', '-d', '--no-deps', '--no-recreate', '--scale', 'worker=4', 'worker'],
        'rekonsyliacja workerów Docker',
    )]

    unchanged = updater.ensure_worker_capacity(3)
    assert unchanged['changed'] is False
    assert unchanged['reconciled'] is True
    assert unchanged['worker_count'] == 4
    assert calls[-1] == (
        ['docker', 'compose', 'up', '-d', '--no-deps', '--no-recreate', '--scale', 'worker=4', 'worker'],
        'rekonsyliacja workerów Docker',
    )
    assert len(calls) == 2


def test_ensure_worker_capacity_starts_missing_systemd_units(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'INSTALL_MODE', 'systemd')
    config = updater.CONFIG_DIR / 'backend.env'
    config.write_text('CP_WORKER_COUNT=1\n', encoding='utf-8')
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stdout = ''

    monkeypatch.setattr(updater.shutil, 'which', lambda name: '/bin/systemctl' if name == 'systemctl' else None)
    monkeypatch.setattr(updater, '_service_active', lambda unit: unit == 'cloudportal-worker@1.service')
    monkeypatch.setattr(
        updater.subprocess,
        'run',
        lambda command, **kwargs: Result(0 if command[-1] == 'cloudportal-worker@1.service' else 1),
    )
    monkeypatch.setattr(
        updater,
        '_run_worker_command',
        lambda command, label, timeout=300: calls.append((command, label)) or '',
    )
    monkeypatch.setattr(updater, '_schedule_api_refresh_after_worker_change', lambda: None)

    result = updater.ensure_worker_capacity(3)

    assert result['changed'] is True
    assert result['reconciled'] is True
    assert result['previous_worker_count'] == 1
    assert result['worker_count'] == 3
    assert updater.parse_kv(config)['CP_WORKER_COUNT'] == '3'
    assert [call[0] for call in calls] == [[
        '/bin/systemctl',
        'enable',
        '--now',
        'cloudportal-worker@2.service',
        'cloudportal-worker@3.service',
    ]]
    assert calls[0][1] == 'rekonsyliacja workerów systemd'


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


def test_candidate_build_context_modes_are_readable_by_non_root_image_user(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    source = tmp_path / 'candidate'
    nested = source / 'app'
    nested.mkdir(parents=True)
    regular = nested / 'module.py'
    executable = source / 'install.sh'
    regular.write_text('VALUE = 1\n')
    executable.write_text('#!/bin/sh\n')
    source.chmod(0o700)
    nested.chmod(0o700)
    regular.chmod(0o600)
    executable.chmod(0o700)

    updater._normalize_candidate_build_context(source)

    assert source.stat().st_mode & 0o777 == 0o755
    assert nested.stat().st_mode & 0o777 == 0o755
    assert regular.stat().st_mode & 0o777 == 0o644
    assert executable.stat().st_mode & 0o777 == 0o755


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


def test_automatic_update_failure_records_retry_cooldown(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    target = 'f' * 40
    monkeypatch.setattr(updater, 'check_remote', lambda ref, update_context=False: {
        'update_available': True,
        'target_sha': target,
        'target_version': target[:12],
        'target_commit_at': '2026-09-29T07:00:00Z',
    })
    monkeypatch.setattr(updater, 'ensure_current_installation_healthy', lambda: None)
    monkeypatch.setattr(
        updater,
        'wait_for_required_ci',
        lambda sha, settings: (_ for _ in ()).throw(RuntimeError('candidate CI failed')),
    )

    updater.run_update('main', automatic=True)

    state = updater.load_state()
    assert state['status'] == 'failed'
    assert state['automatic'] is True
    assert state['consecutive_failures'] == 1
    assert state['failed_target_sha'] == target
    assert state['retry_not_before']
    assert state['last_failure_at']
    assert state['last_result'] == 'failed'


def test_automatic_update_defers_same_failed_sha_until_cooldown_expires(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    target = '9' * 40
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.atomic_json(updater.STATE_FILE, {
        **updater.default_state(),
        'status': 'failed',
        'failed_target_sha': target,
        'consecutive_failures': 2,
        'retry_not_before': '2999-01-01T00:00:00+00:00',
    })
    monkeypatch.setattr(updater, 'check_remote', lambda ref, update_context=False: {
        'update_available': True,
        'target_sha': target,
        'target_version': target[:12],
        'target_commit_at': '2026-09-29T07:00:00Z',
    })
    monkeypatch.setattr(
        updater,
        'ensure_current_installation_healthy',
        lambda: (_ for _ in ()).throw(AssertionError('health preflight must not run during cooldown')),
    )
    monkeypatch.setattr(
        updater,
        'wait_for_required_ci',
        lambda *args: (_ for _ in ()).throw(AssertionError('CI must not run during cooldown')),
    )

    updater.run_update('main', automatic=True)

    state = updater.load_state()
    assert state['status'] == 'deferred'
    assert state['phase'] == 'cooldown'
    assert state['update_available'] is True
    assert state['automatic'] is True
    assert state['consecutive_failures'] == 2
    assert state['failed_target_sha'] == target


def test_update_status_compact_omits_large_history(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.atomic_json(updater.STATE_FILE, {
        **updater.default_state(),
        'status': 'running',
        'phase': 'install',
        'progress': 50,
        'events': [{'at': updater.utcnow(), 'phase': 'install', 'progress': 50, 'message': 'x' * 1000}] * 120,
        'output': ['y' * 2000] * 160,
    })
    updater.update_thread = None

    compact = updater.status_payload(compact=True)

    assert 'events' not in compact
    assert 'output' not in compact
    assert compact['status'] in {'failed', 'running'}
    assert len(json.dumps(compact).encode('utf-8')) < 65536


def test_tls_letsencrypt_detection_and_activation_updates_public_host(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'INSTALL_MODE', 'docker')

    config = updater.CONFIG_DIR / 'docker.env'
    config.write_text(
        'CP_PUBLIC_HOST=100.118.132.40\n'
        'CP_HTTPS_PORT=8443\n'
        'CP_WORKER_COUNT=1\n',
        encoding='utf-8',
    )

    live = tmp_path / 'letsencrypt' / 'live'
    lineage = live / 'kynlab.ddnsfree.com'
    lineage.mkdir(parents=True)
    cert = lineage / 'fullchain.pem'
    key = lineage / 'privkey.pem'
    cert.write_text('CERT', encoding='utf-8')
    key.write_text('KEY', encoding='utf-8')

    monkeypatch.setattr(updater, 'LETSENCRYPT_LIVE_DIR', live)
    monkeypatch.setattr(updater, 'CERTBOT_DEPLOY_HOOK', tmp_path / 'renewal-hooks' / 'cloudportal-backed')
    monkeypatch.setattr(
        updater,
        '_certificate_details',
        lambda path, hostname=None: {
            'present': True,
            'valid': True,
            'matches_hostname': hostname == 'kynlab.ddnsfree.com',
            'dns_names': ['kynlab.ddnsfree.com'],
            'ip_addresses': [],
            'not_after': 'Dec 31 23:59:59 2026 GMT',
        },
    )

    captured = {}

    def activate(cert_bytes, key_bytes, **kwargs):
        captured.update(kwargs)
        return {'hostname': kwargs['hostname'], 'source': kwargs['source_type']}

    monkeypatch.setattr(updater, '_activate_tls_material', activate)

    discovered = updater.letsencrypt_candidates()
    assert [item['lineage'] for item in discovered['items']] == ['kynlab.ddnsfree.com']
    assert discovered['items'][0]['path'].endswith('/etc/letsencrypt/live/kynlab.ddnsfree.com') is False
    assert discovered['items'][0]['certificate']['dns_names'] == ['kynlab.ddnsfree.com']

    result = updater.apply_letsencrypt_tls({'lineage': 'kynlab.ddnsfree.com'})
    assert result['hostname'] == 'kynlab.ddnsfree.com'
    assert captured['source_type'] == 'letsencrypt'
    assert captured['source_path'] == str(lineage)
    assert captured['configure_renewal_hook'] is True


def test_tls_status_does_not_expose_private_key_content(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'INSTALL_MODE', 'systemd')
    (updater.CONFIG_DIR / 'public.conf').write_text('host=portal.example.test\nport=8443\n', encoding='utf-8')
    tls = updater.CONFIG_DIR / 'tls'
    tls.mkdir()
    (tls / 'server.crt').write_text('CERTIFICATE', encoding='utf-8')
    (tls / 'server.key').write_text('TOP-SECRET-PRIVATE-KEY', encoding='utf-8')
    (tls / 'certificate-source').write_text('custom\n', encoding='utf-8')
    monkeypatch.setattr(
        updater,
        '_certificate_details',
        lambda path, hostname=None: {'present': True, 'valid': True, 'matches_hostname': True},
    )
    monkeypatch.setattr(updater, 'CERTBOT_DEPLOY_HOOK', tmp_path / 'missing-hook')

    payload = updater._tls_status_payload()

    assert payload['hostname'] == 'portal.example.test'
    assert payload['key_present'] is True
    assert 'TOP-SECRET-PRIVATE-KEY' not in str(payload)


def test_tls_activation_persists_letsencrypt_as_custom_for_installer(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'INSTALL_MODE', 'docker')
    config = updater.CONFIG_DIR / 'docker.env'
    config.write_text(
        'CP_PUBLIC_HOST=100.118.132.40\nCP_HTTPS_PORT=8443\nCP_WORKER_COUNT=1\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(updater, 'CERTBOT_DEPLOY_HOOK', tmp_path / 'certbot-hook')
    monkeypatch.setattr(updater, '_validate_tls_pair', lambda cert, key, hostname: {})
    monkeypatch.setattr(updater, '_validate_proxy_tls', lambda: None)
    monkeypatch.setattr(updater, '_reload_proxy_tls', lambda: None)
    monkeypatch.setattr(
        updater,
        '_certificate_details',
        lambda cert, hostname=None: {'present': True, 'valid': True, 'matches_hostname': True},
    )

    result = updater._activate_tls_material(
        b'CERT',
        b'KEY',
        hostname='kynlab.ddnsfree.com',
        source_type='letsencrypt',
        source_path='/etc/letsencrypt/live/kynlab.ddnsfree.com',
        configure_renewal_hook=True,
    )

    tls = updater.CONFIG_DIR / 'tls'
    assert updater.parse_kv(config)['CP_PUBLIC_HOST'] == 'kynlab.ddnsfree.com'
    assert (tls / 'source').read_text().strip() == 'custom'
    metadata = updater._read_json(tls / 'cloudportal-source.json')
    assert metadata['type'] == 'letsencrypt'
    assert metadata['path'] == '/etc/letsencrypt/live/kynlab.ddnsfree.com'
    assert result['hostname'] == 'kynlab.ddnsfree.com'
    hook = (tmp_path / 'certbot-hook').read_text(encoding='utf-8')
    assert 'ACTIVE_LINEAGE="/etc/letsencrypt/live/kynlab.ddnsfree.com"' in hook
    assert 'RENEWED_LINEAGE' in hook
    assert '/tls/sync' in hook


def test_tls_activation_is_blocked_during_active_update(tmp_path, monkeypatch):
    updater = load_update_service_module(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, 'update_operation_active', lambda: True)

    try:
        updater._activate_tls_material(
            b'CERT',
            b'KEY',
            hostname='kynlab.ddnsfree.com',
            source_type='custom',
        )
    except RuntimeError as exc:
        assert 'aktywnej aktualizacji' in str(exc)
    else:
        raise AssertionError('TLS mutation must be blocked while updater is active')


def test_tls_validation_rejects_certificate_before_not_before(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    updater = load_update_service_module(tmp_path, monkeypatch)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%b %d %H:%M:%S %Y GMT')

    def fake_tls_command(command, **kwargs):
        if '-startdate' in command:
            return ('notBefore=' + future + '\n').encode()
        return b''

    monkeypatch.setattr(updater, '_run_tls_command', fake_tls_command)
    monkeypatch.setattr(updater, '_certificate_key_matches', lambda cert, key: True)
    monkeypatch.setattr(updater, '_certificate_matches_hostname', lambda cert, hostname: True)

    cert = tmp_path / 'server.crt'
    key = tmp_path / 'server.key'
    cert.write_text('CERT', encoding='utf-8')
    key.write_text('KEY', encoding='utf-8')

    try:
        updater._validate_tls_pair(cert, key, 'kynlab.ddnsfree.com')
    except ValueError as exc:
        assert 'nie jest jeszcze ważny' in str(exc)
    else:
        raise AssertionError('Future notBefore certificate must be rejected')
