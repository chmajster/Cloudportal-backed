"""Cron tests do not install packages, contact GitHub or change host crontabs."""
import ast
import io
import subprocess
import types
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / 'install.sh'


@pytest.fixture
def source():
    return INSTALLER.read_text(encoding='utf-8')


@pytest.fixture
def client(source):
    text = source.split("cat <<'PY_CRON_UPDATE'\n", 1)[1].split('\nPY_CRON_UPDATE\n', 1)[0]
    ast.parse(text)
    module = types.ModuleType('cloudportal_cron_test_client')
    exec(compile(text, 'cron-update.py', 'exec'), module.__dict__)
    return module


def shell(source, command):
    block = '# Cron is opt-in' + source.split('# Cron is opt-in', 1)[1].split('\ndocker_root=', 1)[0]
    return subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + block + '\n' + command],
                          text=True, capture_output=True, timeout=10, check=False)


@pytest.mark.parametrize('hours', [1, 2, 3, 4, 6, 8, 12, 24])
def test_valid_intervals(source, hours):
    result = shell(source, 'auto_update_cron_contents ' + str(hours))
    assert result.returncode == 0, result.stderr
    expected = '0 0 * * *' if hours == 24 else '0 */{} * * *'.format(hours)
    jobs = [line for line in result.stdout.splitlines() if ' root ' in line]
    assert len(jobs) == 1
    assert jobs[0].startswith(expected + ' root /usr/local/sbin/cloudportal-auto-update ')
    assert result.stdout.endswith('\n')
    assert '\x1b' not in result.stdout


@pytest.mark.parametrize('hours', ['0', '5', '7', '13', '25', '-1', '12h', '012', '', '12;echo injected'])
def test_invalid_intervals(source, hours):
    import shlex
    result = shell(source, 'auto_update_cron_contents ' + shlex.quote(hours))
    assert result.returncode == 2
    assert result.stdout == ''


def test_remove_only_owned_files(source):
    result = shell(source, "ui_ok() { :; }; ui_info() { :; }; rm() { printf '%s\\n' \"$@\"; }; auto_update_remove")
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        '-f', '/etc/cron.d/cloudportal-auto-update', '/usr/local/sbin/cloudportal-auto-update',
        '/usr/local/lib/cloudportal-updater/cron-update.py', '/etc/logrotate.d/cloudportal-auto-update',
    ]


def prepare_client(client, monkeypatch):
    monkeypatch.setattr(client.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(client.Path, 'is_file', lambda self: True)
    monkeypatch.setattr(client, 'open', lambda *a, **kw: io.StringIO(), raising=False)
    monkeypatch.setattr(client.fcntl, 'flock', lambda *a: None)


@pytest.mark.parametrize('docker', [False, True])
def test_submit_preserves_saved_settings(client, monkeypatch, capsys, docker):
    prepare_client(client, monkeypatch)
    calls = []
    def request(*args):
        calls.append(args)
        return {'accepted': True, 'already_running': False}
    monkeypatch.setattr(client, 'request_updater', request)
    monkeypatch.setattr(client, 'wait_for_result', lambda docker_mode: 0)
    assert client.main(['--docker'] if docker else []) == 0
    assert calls == [(docker, '/run', {'automatic': True})]
    assert 'Zlecono' in capsys.readouterr().out


def test_configure_only_disables_internal_scheduler(client, monkeypatch):
    prepare_client(client, monkeypatch)
    calls = []
    def request(*args):
        calls.append(args)
        return {'enabled': False, 'ref': 'stable', 'interval_hours': 24}
    monkeypatch.setattr(client, 'request_updater', request)
    assert client.main(['--configure']) == 0
    assert calls == [(False, '/settings', {'enabled': False})]


def test_installer_busy_skips_request(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    def busy(*args):
        raise BlockingIOError()
    monkeypatch.setattr(client.fcntl, 'flock', busy)
    monkeypatch.setattr(client, 'request_updater', lambda *a: pytest.fail('must not call updater'))
    assert client.main([]) == 0
    assert 'Instalator jest zajety' in capsys.readouterr().out


def test_updater_busy_is_not_failure(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    monkeypatch.setattr(client, 'request_updater', lambda *a: {'accepted': False, 'already_running': True})
    assert client.main([]) == 0
    assert 'juz trwa' in capsys.readouterr().out


def test_missing_installation_never_reinstalls(client, monkeypatch):
    prepare_client(client, monkeypatch)
    monkeypatch.setattr(client.Path, 'is_file', lambda self: False)
    monkeypatch.setattr(client, 'request_updater', lambda *a: pytest.fail('must not call updater'))
    assert client.main([]) == 0
    assert client.main(['--configure']) == 1


def test_requires_root(client, monkeypatch):
    monkeypatch.setattr(client.os, 'geteuid', lambda: 1000)
    assert client.main([]) == 1


def test_errors_do_not_leak_secrets(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    def fail(*args):
        raise OSError('Authorization: super-secret-do-not-log')
    monkeypatch.setattr(client, 'request_updater', fail)
    assert client.main([]) == 1
    output = capsys.readouterr().out
    assert '[FAIL]' in output
    assert 'super-secret' not in output
    assert '\x1b' not in output


@pytest.mark.parametrize('docker', [False, True])
def test_transport_and_token_header(client, monkeypatch, docker):
    token = 'a' * 64
    monkeypatch.setattr(client.Path, 'read_text', lambda *a, **kw: token)
    class Response:
        status = 202
        def read(self, limit):
            return b'{"accepted": true}'
    class Connection:
        def __init__(self):
            self.closed = False
            self.call = None
        def request(self, *args, **kwargs):
            self.call = (args, kwargs)
        def getresponse(self):
            return Response()
        def close(self):
            self.closed = True
    conn = Connection()
    constructor_calls = []
    def create(*args, **kwargs):
        constructor_calls.append((args, kwargs))
        return conn
    monkeypatch.setattr(client.http.client, 'HTTPConnection', create)
    monkeypatch.setattr(client, 'UnixHTTPConnection', create)
    assert client.request_updater(docker, '/run', {}) == {'accepted': True}
    assert conn.closed
    assert conn.call[0] == ('POST', '/run')
    assert conn.call[1]['headers']['X-Updater-Token'] == token
    assert conn.call[1]['body'] == b'{}'
    if docker:
        assert constructor_calls == [(('/run/cloudportal-updater-docker/updater.sock',), {})]
    else:
        assert constructor_calls == [(('127.0.0.1', 8766), {'timeout': 15})]


def test_wait_for_result_reports_success(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    states = iter([
        {'status': 'running', 'phase': 'backup', 'progress': 14, 'operation_active': True},
        {'status': 'success', 'phase': 'complete', 'progress': 100, 'operation_active': False,
         'current_version': 'abc123def456'},
    ])
    monkeypatch.setattr(client, 'request_updater', lambda *a, **kw: next(states))
    monkeypatch.setattr(client.time, 'sleep', lambda *_: None)
    assert client.wait_for_result(False, timeout_seconds=60, poll_seconds=0) == 0
    output = capsys.readouterr().out
    assert 'backup (14%)' in output
    assert 'abc123def456' in output


def test_wait_for_result_reports_failure(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    monkeypatch.setattr(client, 'request_updater', lambda *a, **kw: {
        'status': 'failed', 'phase': 'runtime_preflight', 'progress': 18, 'operation_active': False,
    })
    assert client.wait_for_result(False, timeout_seconds=60, poll_seconds=0) == 1
    assert 'runtime_preflight' in capsys.readouterr().out


def test_wait_for_result_accepts_cooldown(client, monkeypatch, capsys):
    prepare_client(client, monkeypatch)
    monkeypatch.setattr(client, 'request_updater', lambda *a, **kw: {
        'status': 'deferred', 'phase': 'cooldown', 'progress': 8, 'operation_active': False,
        'retry_not_before': '2026-09-29T12:00:00+00:00',
    })
    assert client.wait_for_result(True, timeout_seconds=60, poll_seconds=0) == 0
    assert 'odroczony' in capsys.readouterr().out


def test_wait_for_result_requests_compact_status(client, monkeypatch):
    prepare_client(client, monkeypatch)
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            'status': 'up_to_date',
            'phase': 'up_to_date',
            'progress': 100,
            'operation_active': False,
        }
    monkeypatch.setattr(client, 'request_updater', request)
    assert client.wait_for_result(False, timeout_seconds=60, poll_seconds=0) == 0
    assert calls == [((False, '/status?compact=1'), {'method': 'GET'})]
