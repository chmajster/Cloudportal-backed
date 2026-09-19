from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'install.sh').read_text(encoding='utf-8')


def test_installer_takes_over_competing_installation_by_default():
    assert 'takeover_running_install=1' in INSTALLER
    assert 'Another installation is running. Stopping the Cloudportal application and taking over the installer lock...' in INSTALLER
    assert 'stop_cloudportal_application' in INSTALLER
    assert 'terminate_previous_installer' in INSTALLER
    assert 'force_stop_previous_installer' in INSTALLER
    assert 'kill -TERM "$owner"' in INSTALLER
    assert 'kill -KILL "$owner"' in INSTALLER
    assert "flock -n 9" in INSTALLER


def test_installer_stops_cloudportal_services_before_takeover():
    assert 'systemctl stop cloudportal-updater.service' in INSTALLER
    assert 'systemctl stop cloudportal-dispatcher.service cloudportal-api.service' in INSTALLER
    assert "'cloudportal-worker@*.service'" in INSTALLER


def test_installer_records_and_discovers_lock_owner():
    assert 'record_install_lock_owner' in INSTALLER
    assert 'lslocks -n -o PID,PATH' in INSTALLER
    assert "lock_file=/run/cloudportal-install.lock" in INSTALLER
    assert 'exec 9>>"$lock_file"' in INSTALLER


def test_installer_allows_disabling_takeover():
    assert '--no-takeover) takeover_running_install=0' in INSTALLER
    assert '--takeover) takeover_running_install=1' in INSTALLER
    assert 'Re-run without --no-takeover to stop it automatically.' in INSTALLER
