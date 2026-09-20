import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_updater():
    path = ROOT / 'scripts' / 'update-service.py'
    spec = importlib.util.spec_from_file_location('cloudportal_update_service_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def commit_payload(sha, committed_at):
    return {
        'sha': sha,
        'commit': {
            'committer': {'date': committed_at},
            'author': {'date': committed_at},
        },
    }


def test_target_ahead_is_newer_version(monkeypatch):
    updater = load_updater()
    current = '1' * 40
    target = {'sha': '2' * 40, 'committed_at': '2026-09-19T01:00:00Z'}

    def fake_json(url, settings):
        if '/compare/' in url:
            return {'status': 'ahead', 'ahead_by': 3, 'behind_by': 0}
        if '/commits/' in url:
            return commit_payload(current, '2026-09-18T20:00:00Z')
        raise AssertionError(url)

    monkeypatch.setattr(updater, '_http_json', fake_json)
    result = updater.commit_order(current, target, {})

    assert result['relation'] == 'target_newer'
    assert result['update_available'] is True
    assert result['ahead_by'] == 3
    assert result['behind_by'] == 0


def test_target_behind_is_not_an_update(monkeypatch):
    updater = load_updater()
    current = '3' * 40
    target = {'sha': '2' * 40, 'committed_at': '2026-09-18T20:00:00Z'}

    def fake_json(url, settings):
        if '/compare/' in url:
            return {'status': 'behind', 'ahead_by': 0, 'behind_by': 2}
        if '/commits/' in url:
            return commit_payload(current, '2026-09-19T01:00:00Z')
        raise AssertionError(url)

    monkeypatch.setattr(updater, '_http_json', fake_json)
    result = updater.commit_order(current, target, {})

    assert result['relation'] == 'current_newer'
    assert result['update_available'] is False
    assert result['behind_by'] == 2


def test_identical_commit_is_same_version(monkeypatch):
    updater = load_updater()
    sha = '4' * 40

    def should_not_call(*args, **kwargs):
        raise AssertionError('GitHub compare must not run for identical SHA')

    monkeypatch.setattr(updater, '_http_json', should_not_call)
    result = updater.commit_order(
        sha,
        {'sha': sha, 'committed_at': '2026-09-19T01:00:00Z'},
        {},
    )

    assert result['relation'] == 'identical'
    assert result['update_available'] is False
    assert result['ahead_by'] == 0
    assert result['behind_by'] == 0


def test_diverged_history_uses_newer_commit_timestamp(monkeypatch):
    updater = load_updater()
    current = '5' * 40
    target = {'sha': '6' * 40, 'committed_at': '2026-09-19T02:00:00Z'}

    def fake_json(url, settings):
        if '/compare/' in url:
            return {'status': 'diverged', 'ahead_by': 2, 'behind_by': 1}
        if '/commits/' in url:
            return commit_payload(current, '2026-09-19T01:00:00Z')
        raise AssertionError(url)

    monkeypatch.setattr(updater, '_http_json', fake_json)
    result = updater.commit_order(current, target, {})

    assert result['relation'] == 'target_newer_diverged'
    assert result['update_available'] is True


def test_unknown_local_commit_accepts_channel_head_as_version():
    updater = load_updater()
    target = {'sha': '7' * 40, 'committed_at': '2026-09-19T02:00:00Z'}

    result = updater.commit_order('', target, {})

    assert result['relation'] == 'unknown_current'
    assert result['update_available'] is True


def prepare_updater_state(updater, tmp_path, monkeypatch, commit='1' * 40):
    config = tmp_path / 'etc'
    data = tmp_path / 'data'
    current = tmp_path / 'app' / 'current'
    config.mkdir()
    data.mkdir()
    current.mkdir(parents=True)
    settings_file = config / 'updater.json'
    settings_file.write_text(json.dumps({
        'enabled': False,
        'interval_hours': 24,
        'ref': 'main',
        'github_token_file': '',
        'github_config': '',
    }))
    (current / '.cloudportal-release.json').write_text(json.dumps({
        'commit_sha': commit,
        'ref': 'main',
    }))
    monkeypatch.setattr(updater, 'SETTINGS_FILE', settings_file)
    monkeypatch.setattr(updater, 'STATE_DIR', data / 'update')
    monkeypatch.setattr(updater, 'STATE_FILE', data / 'update' / 'state.json')
    monkeypatch.setattr(updater, 'CURRENT_LINK', current)
    updater.atomic_json(updater.STATE_FILE, updater.default_state())


def test_update_context_check_does_not_revert_public_state_to_update_available(tmp_path, monkeypatch):
    updater = load_updater()
    prepare_updater_state(updater, tmp_path, monkeypatch)
    target = {'sha': '2' * 40, 'committed_at': '2026-09-20T01:00:00Z'}

    monkeypatch.setattr(updater, 'remote_commit', lambda ref, settings: target)
    monkeypatch.setattr(updater, 'commit_order', lambda current, remote, settings: {
        'relation': 'target_newer',
        'update_available': True,
        'ahead_by': 4,
        'behind_by': 0,
        'current_commit_at': '2026-09-19T01:00:00Z',
    })

    result = updater.check_remote('main', update_context=True)
    state = updater.load_state()

    assert result['update_available'] is True
    assert state['status'] == 'running'
    assert state['phase'] == 'preflight'
    assert state['progress'] == 8
    assert state['finished_at'] is None
    assert state['message'] == 'Nowszy commit potwierdzony. Przygotowanie do instalacji.'


def test_runtime_state_marks_live_update_thread_as_running(tmp_path, monkeypatch):
    updater = load_updater()
    prepare_updater_state(updater, tmp_path, monkeypatch)
    updater.save_state(
        status='update_available',
        phase='available',
        progress=8,
        message='Dostępny jest nowszy commit.',
    )

    class AliveThread:
        def is_alive(self):
            return True

    updater.update_thread = AliveThread()
    state = updater.runtime_state()

    assert state['operation_active'] is True
    assert state['status'] == 'running'
    assert state['phase'] == 'preflight'
    assert state['finished_at'] is None
