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


def test_updater_driven_install_keeps_sidecar_alive_and_reloads_it_after_success():
    assert "if ((update_in_progress)); then\n    ui_info 'Serwis updatera pozostaje aktywny na czas aktualizacji.'" in INSTALLER
    assert 'systemd-run --quiet --collect --unit="$updater_reload_unit" --on-active=8s' in INSTALLER
    assert '/bin/systemctl restart cloudportal-updater.service' in INSTALLER


def test_update_service_is_stdlib_only_and_stable():
    source = (ROOT / 'scripts' / 'update-service.py').read_text()
    assert 'ThreadingHTTPServer' in source
    assert '127.0.0.1' in source
    assert 'CLOUDPORTAL_RELEASE_SHA' in source
    assert 'cloudportal-backup' in source


def test_backup_and_restore_include_system_sbin_for_runuser():
    backup = (ROOT / 'scripts' / 'backend-backup.py').read_text()
    restore = (ROOT / 'scripts' / 'backend-restore.py').read_text()
    expected_path = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    for source in (backup, restore):
        assert expected_path in source
        assert "shutil.which('runuser', path=env['PATH'])" in source
        assert "['runuser', '-u'" not in source


def test_backup_streams_pg_dump_into_root_owned_file():
    source = (ROOT / 'scripts' / 'backend-backup.py').read_text()
    assert "'--file'" not in source
    assert "with dump.open('xb') as stream:" in source
    assert "stdout=stream" in source
    assert "dump.unlink(missing_ok=True)" in source



def test_updater_install_preserves_moving_channel_while_source_is_sha_pinned():
    updater = (ROOT / 'scripts' / 'update-service.py').read_text()
    assert 'download_installer(target_sha, installer)' in updater
    assert 'installer_args(target_sha)' in updater
    assert 'CLOUDPORTAL_UPDATE_CHANNEL_REF' in updater
    assert 'wait_for_required_ci(target_sha, settings)' in updater
    assert 'validate_candidate(target_sha, settings)' in updater

    assert 'update_channel_ref=${CLOUDPORTAL_UPDATE_CHANNEL_REF:-}' in INSTALLER
    assert 'release_ref=${update_channel_ref:-$ref}' in INSTALLER
    assert '"$repo" "$release_ref" "$release_sha" "$archive_sha"' in INSTALLER
    assert '"$updater_config" "$release_ref"' in INSTALLER
    assert "data.setdefault('require_ci', True)" in INSTALLER
    assert "data.setdefault('candidate_validation', True)" in INSTALLER



def test_installer_enables_runtime_preflight_by_default():
    assert "data.setdefault('runtime_preflight', True)" in INSTALLER


def test_docker_installer_provisions_host_updater_and_pins_release_metadata():
    assert 'docker_prepare_updater_config()' in INSTALLER
    assert 'docker_activate_updater()' in INSTALLER
    assert 'docker_updater_status_probe()' in INSTALLER
    assert 'CP_UPDATER_INSTALL_MODE=docker' in INSTALLER
    assert 'CP_UPDATER_SOCKET=$docker_updater_runtime/updater.sock' in INSTALLER
    assert 'CP_UPDATER_HOST_CONFIG_DIR=$docker_config' in INSTALLER
    assert 'CP_UPDATER_RUNTIME_DIR=$docker_updater_runtime' in INSTALLER
    assert 'updater-status.token' in INSTALLER
    assert 'cloudportal-updater.service' in INSTALLER
    assert '"$release/.cloudportal-release.json"' in INSTALLER
    assert 'CLOUDPORTAL_RELEASE_SHA' in INSTALLER
    assert "Kanał /update-status i token statusu działają poprawnie." in INSTALLER


def test_update_service_supports_docker_runtime():
    source = (ROOT / 'scripts' / 'update-service.py').read_text()
    assert 'CP_UPDATER_INSTALL_MODE' in source
    assert 'BIND_HOST' in source
    assert 'SOCKET_PATH' in source
    assert 'ThreadingUnixHTTPServer' in source
    assert 'INSTALL_MODE == "docker"' in source
    assert '"--docker", "--non-interactive"' in source
    assert '_docker_pre_update_backup()' in source
    assert 'pg_dump' in source
    assert 'docker-health:' in source
    assert 'ThreadingHTTPServer((BIND_HOST, PORT), Handler)' in source
    assert 'ThreadingUnixHTTPServer(str(socket_path), Handler)' in source


def test_application_updater_client_supports_unix_socket():
    source = (ROOT / 'app' / 'updates' / 'service.py').read_text()
    assert 'CP_UPDATER_UNIX_SOCKET' in source
    assert 'socket.AF_UNIX' in source
    assert '_UnixHTTPConnection' in source
    assert 'if UPDATER_UNIX_SOCKET:' in source
