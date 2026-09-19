from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'install.sh').read_text()


def test_installer_installs_independent_update_service():
    assert 'cloudportal-updater.service' in INSTALLER
    assert '/usr/local/lib/cloudportal-updater/update-service.py' in INSTALLER
    assert '127.0.0.1:8766' in INSTALLER
    assert 'location = /update-status' in INSTALLER


def test_installer_emits_machine_readable_update_progress():
    assert '::cloudportal-progress::' in INSTALLER
    assert 'CLOUDPORTAL_UPDATE_IN_PROGRESS' in INSTALLER


def test_update_service_is_stdlib_only_and_stable():
    source = (ROOT / 'scripts' / 'update-service.py').read_text()
    assert 'ThreadingHTTPServer' in source
    assert '127.0.0.1' in source
    assert 'CLOUDPORTAL_RELEASE_SHA' in source
    assert 'cloudportal-backup' in source
