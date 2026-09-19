import importlib.util
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
