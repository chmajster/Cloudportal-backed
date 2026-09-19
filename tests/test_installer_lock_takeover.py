from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'install.sh').read_text(encoding='utf-8')


def test_installer_takes_over_competing_installation_by_default():
    assert 'takeover_running_install=1' in INSTALLER
    assert 'Wykryto inną instalację. Zatrzymuję Cloudportal i przejmuję blokadę instalatora...' in INSTALLER
    assert 'stop_cloudportal_application' in INSTALLER
    assert 'stop_previous_installer TERM' in INSTALLER
    assert 'stop_previous_installer KILL' in INSTALLER
    assert 'terminate_process_tree' in INSTALLER


def test_installer_uses_non_leaking_lock_holder():
    assert 'flock --exclusive --nonblock --close "$lock_file"' in INSTALLER
    assert 'lock_owner_file=/run/cloudportal-install.owner' in INSTALLER
    assert 'try_acquire_install_lock' in INSTALLER
    assert 'while kill -0 "$installer_pid"' in INSTALLER
    assert 'exec 9>>"$lock_file"' not in INSTALLER
    assert 'flock -n 9' not in INSTALLER


def test_installer_discovers_inherited_lock_holders_through_proc():
    assert 'proc_lock_pids' in INSTALLER
    assert 'for fd in /proc/[0-9]*/fd/*' in INSTALLER
    assert '"$lock_file (deleted)"' in INSTALLER
    assert 'lslocks -n -o PID,PATH' in INSTALLER


def test_installer_stops_cloudportal_services_before_takeover():
    assert 'systemctl stop cloudportal-updater.service' in INSTALLER
    assert 'systemctl stop cloudportal-dispatcher.service cloudportal-api.service' in INSTALLER
    assert "'cloudportal-worker@*.service'" in INSTALLER


def test_installer_allows_disabling_takeover():
    assert '--no-takeover) takeover_running_install=0' in INSTALLER
    assert '--takeover) takeover_running_install=1' in INSTALLER
    assert '--no-takeover zabrania jej zatrzymania' in INSTALLER


def test_force_uninstall_preserves_data_by_default():
    assert '--force-uninstall|--uninstall) force_uninstall=1' in INSTALLER
    assert 'force_uninstall_cloudportal' in INSTALLER
    assert 'rm -rf "$app_root"' in INSTALLER
    assert 'Baza, /etc/cloudportal-backed i /var/lib/cloudportal-backed zostały zachowane.' in INSTALLER
    assert 'systemctl disable --now' in INSTALLER
    assert '/etc/nginx/conf.d/cloudportal-backed.conf' in INSTALLER


def test_force_uninstall_can_purge_all_local_data():
    assert '--purge-data) purge_data=1' in INSTALLER
    assert '--purge-data requires --force-uninstall.' in INSTALLER
    assert 'dropdb --if-exists cloudportal' in INSTALLER
    assert 'dropuser --if-exists cloudportal' in INSTALLER
    assert 'rm -rf "$config" "$data" /var/backups/cloudportal-backed' in INSTALLER
    assert 'userdel cloudportal' in INSTALLER
