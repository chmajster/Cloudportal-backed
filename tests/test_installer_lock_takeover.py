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


def test_uninstall_preserves_data_by_default_and_removes_runtime_integrations():
    assert '--uninstall) uninstall_mode=1' in INSTALLER
    assert '--force-uninstall) uninstall_mode=1; assume_yes=1' in INSTALLER
    assert 'uninstall_cloudportal()' in INSTALLER
    assert 'acquire_install_lock' in INSTALLER
    assert 'rm -rf "$app_root" /usr/local/lib/cloudportal-updater' in INSTALLER
    assert "Baza, konfiguracja i dane aplikacji zostały zachowane." in INSTALLER
    assert 'cloudportal-updater.timer' in INSTALLER
    assert 'systemctl disable --now' in INSTALLER
    assert '/etc/nginx/conf.d/cloudportal-backed.conf' in INSTALLER
    assert "Pakiety współdzielone PostgreSQL, Redis/Valkey, Nginx, Terraform i Ansible nie są automatycznie usuwane." in INSTALLER


def test_uninstall_requires_confirmation_and_supports_noninteractive_yes():
    assert 'confirm_uninstall()' in INSTALLER
    assert "Wpisz USUN, aby potwierdzić pełne usunięcie" in INSTALLER
    assert "Odinstalować runtime Cloudportal i zachować bazę oraz dane? [y/N]" in INSTALLER
    assert "Tryb --non-interactive z --uninstall wymaga jawnego --yes." in INSTALLER
    assert '--yes|-y) assume_yes=1' in INSTALLER


def test_uninstall_can_purge_all_local_data_and_verifies_cleanup():
    assert '--purge-data) purge_data=1' in INSTALLER
    assert '--purge-data wymaga --uninstall.' in INSTALLER
    assert 'dropdb --force --if-exists cloudportal' in INSTALLER
    assert 'dropuser --if-exists cloudportal' in INSTALLER
    assert 'rm -rf "$config" "$data" /var/backups/cloudportal-backed' in INSTALLER
    assert 'userdel cloudportal' in INSTALLER
    assert 'verify_uninstall()' in INSTALLER
    assert "Baza PostgreSQL cloudportal nadal istnieje." in INSTALLER
