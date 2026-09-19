import json

from app.config import settings
from app.version import build_commit, build_version


def reset_version_cache():
    build_commit.cache_clear()


def test_build_version_is_short_git_commit(monkeypatch):
    monkeypatch.setenv('CP_BUILD_COMMIT', 'ABCDEF1234567890ABCDEF1234567890ABCDEF12')
    reset_version_cache()
    try:
        assert build_commit() == 'abcdef1234567890abcdef1234567890abcdef12'
        assert build_version() == 'abcdef123456'
    finally:
        reset_version_cache()


def test_build_version_uses_release_marker(monkeypatch, tmp_path):
    monkeypatch.delenv('CP_BUILD_COMMIT', raising=False)
    current_settings = settings()
    original_source = current_settings.source_dir
    current_settings.source_dir = tmp_path
    (tmp_path / '.cloudportal-release.json').write_text(json.dumps({
        'commit_sha': '1234567890abcdef1234567890abcdef12345678',
    }))
    reset_version_cache()
    try:
        assert build_commit() == '1234567890abcdef1234567890abcdef12345678'
        assert build_version() == '1234567890ab'
    finally:
        current_settings.source_dir = original_source
        reset_version_cache()
