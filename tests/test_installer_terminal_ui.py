from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / 'install.sh'
INSTALLER = INSTALLER_PATH.read_text(encoding='utf-8')


def test_installer_has_structured_terminal_interface():
    assert 'ui_stage()' in INSTALLER
    assert 'ui_ok()' in INSTALLER
    assert 'ui_info()' in INSTALLER
    assert 'ui_warn()' in INSTALLER
    assert 'ui_fail()' in INSTALLER
    for step in range(1, 8):
        assert f'ui_stage {step} 7 ' in INSTALLER
    assert "ui_header 'Podsumowanie'" in INSTALLER


def test_installer_colors_only_when_stdout_is_interactive():
    assert '[[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]' in INSTALLER
    assert 'NO_COLOR=1' in INSTALLER


def test_installer_has_preflight_status_help_and_uninstall_modes():
    assert 'preflight_checks()' in INSTALLER
    assert 'Połączenie HTTPS z api.github.com' in INSTALLER
    assert 'Wolne miejsce:' in INSTALLER
    assert '--status) status_mode=1' in INSTALLER
    assert '--recovery-admin|--recovery-password) recovery_mode=1' in INSTALLER
    assert '--recovery-password-file' in INSTALLER
    assert '--no-auto-repair) docker_auto_repair=0; docker_auto_repair_explicit=1' in INSTALLER
    assert '--uninstall) uninstall_mode=1' in INSTALLER
    assert '--force-uninstall) uninstall_mode=1; assume_yes=1' in INSTALLER
    assert '--yes|-y) assume_yes=1' in INSTALLER
    assert '--help|-h) usage; exit 0' in INSTALLER
    assert 'show_status()' in INSTALLER


def test_installer_without_arguments_opens_action_menu():
    assert 'initial_argc=$#' in INSTALLER
    assert 'interactive_action_menu()' in INSTALLER
    assert '((initial_argc == 0)) || return 0' in INSTALLER
    assert "exec 3<>/dev/tty" in INSTALLER
    assert 'Instalacja / aktualizacja — systemd' in INSTALLER
    assert 'Instalacja / aktualizacja — Docker' in INSTALLER
    assert 'Status / auto-naprawa — Docker' in INSTALLER
    assert 'Odinstaluj — zachowaj bazę i dane' in INSTALLER
    assert 'Odinstaluj całkowicie — usuń bazę i dane' in INSTALLER
    assert 'Odinstaluj Docker — zachowaj wolumeny i konfigurację' in INSTALLER
    assert 'Odinstaluj Docker całkowicie — usuń wolumeny i konfigurację' in INSTALLER
    assert 'Recovery password / konto Administrator — systemd' in INSTALLER
    assert 'Recovery password / konto Administrator — Docker' in INSTALLER
    assert "Wybierz operację [0-10]:" in INSTALLER


def test_installer_without_arguments_requires_tty_in_automation():
    env = dict(os.environ)
    env['NO_COLOR'] = '1'
    result = subprocess.run(
        ['bash', str(INSTALLER_PATH)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        start_new_session=True,
    )
    assert result.returncode == 2
    assert 'Uruchomienie bez parametrów wymaga interaktywnego terminala.' in result.stderr


def test_docker_uninstall_confirmation_uses_controlling_tty():
    assert "printf 'Wpisz USUN, aby trwale usunąć dane Docker: ' >/dev/tty" in INSTALLER
    assert 'IFS= read -r confirmation </dev/tty || true' in INSTALLER
    assert "printf 'Zatrzymać i usunąć kontenery Cloudportal, zachowując wolumeny i konfigurację? [t/N] ' >/dev/tty" in INSTALLER


def test_docker_key_validation_starts_postgres_without_running_migrations_first():
    assert 'docker_compose_for "$release" "$candidate_env" up -d postgres' in INSTALLER
    assert 'exec -T postgres pg_isready -U cloudportal -d cloudportal' in INSTALLER
    assert 'run --rm --no-deps -T bootstrap python -m app.bootstrap --key-only' in INSTALLER
    assert 'bez uruchamiania migracji' in INSTALLER


def test_recovery_admin_uses_stdin_for_password_and_supports_both_runtimes():
    assert 'recovery_prepare_inputs()' in INSTALLER
    assert 'docker_recovery_admin()' in INSTALLER
    assert 'native_recovery_admin()' in INSTALLER
    assert 'python -m app.recovery --username "$recovery_username" --password-stdin' in INSTALLER
    assert '-m app.recovery --username "$recovery_username" --password-stdin' in INSTALLER
    assert 'run --rm --no-deps -T bootstrap' in INSTALLER
    assert 'Tryb --non-interactive recovery wymaga --recovery-password-file FILE.' in INSTALLER
    assert '--password "$recovery_password"' not in INSTALLER


def test_docker_status_validates_every_required_service_and_health():
    assert 'local required_services=(postgres redis migrate api worker dispatcher proxy)' in INSTALLER
    assert 'local long_running_services=(postgres redis api dispatcher proxy)' in INSTALLER
    assert 'docker_compose config --services' in INSTALLER
    assert "health=healthy" in INSTALLER
    assert "migrate: zakończony poprawnie, exit=0" in INSTALLER
    assert 'worker: $running_workers/$expected_workers kontenerów running' in INSTALLER
    assert '/api/v1/health' in INSTALLER
    assert 'Stack Docker kompletny i sprawny:' in INSTALLER
    assert 'Stack Docker jest niekompletny albo co najmniej jedna usługa jest niesprawna.' in INSTALLER


def test_docker_status_auto_repair_restores_only_unhealthy_branches_and_can_be_disabled():
    assert 'docker_repair()' in INSTALLER
    assert 'docker_service_ready()' in INSTALLER
    assert 'docker_wait_service_ready()' in INSTALLER
    assert 'docker_backend_network_probe()' in INSTALLER
    assert 'docker_compose run --rm --no-deps api python -c' in INSTALLER
    assert 'docker_compose up -d --no-deps --force-recreate postgres redis' in INSTALLER
    assert 'docker_compose up -d --no-deps --force-recreate --scale "worker=$expected_workers" worker' in INSTALLER
    assert "Naprawiam zależności selektywnie; zdrowe kontenery nie będą odtwarzane." in INSTALLER
    assert 'docker_compose up -d --no-deps --scale "worker=$expected_workers" worker' in INSTALLER
    assert 'docker_compose restart "$service"' in INSTALLER
    assert 'docker_compose restart proxy' in INSTALLER
    assert 'docker_compose up -d --remove-orphans --scale "worker=$expected_workers"' not in INSTALLER
    assert "Wykryto niesprawny stack Docker; uruchamiam jedną automatyczną próbę naprawy." in INSTALLER
    assert "Auto-naprawa Docker zakończyła się powodzeniem." in INSTALLER
    assert "Auto-naprawa Docker jest wyłączona przez --no-auto-repair." in INSTALLER
    assert "systemctl start docker.service docker.socket" in INSTALLER
    assert '((docker_auto_repair == 0)) || docker_acquire_install_lock' in INSTALLER
    assert 'Auto-naprawa Docker wymaga roota.' in INSTALLER


def test_docker_compose_long_running_infrastructure_has_restart_policy():
    compose = (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
    assert '  postgres:\n    image: postgres:16-alpine\n    restart: unless-stopped' in compose
    assert '  redis:\n    image: redis:7-alpine\n    restart: unless-stopped' in compose
    assert '  proxy:\n    image: nginx:1.28-alpine\n    restart: unless-stopped' in compose


def test_docker_compose_orders_application_startup_by_health():
    compose = (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
    assert compose.count('postgres: {condition: service_healthy}') >= 4
    assert compose.count('redis: {condition: service_healthy}') >= 4
    assert 'api: {condition: service_healthy}' in compose
    assert 'socket.create_connection(("127.0.0.1", 8765), 3)' in compose
    assert 'start_period: 10s' in compose
    assert 'start_period: 5s' in compose

def test_installer_failure_trap_is_actionable_without_dumping_commands():
    assert 'installer_error()' in INSTALLER
    assert 'Etap „$CURRENT_STAGE” przerwany' in INSTALLER
    assert 'journalctl -u cloudportal-api' in INSTALLER
    assert '$BASH_COMMAND' not in INSTALLER
    assert 'sudo docker ps --filter label=com.docker.compose.project=cloudportal-backed' in INSTALLER
    assert 'sudo ./install.sh --docker --status' in INSTALLER


def test_help_is_plain_text_without_ansi_sequences():
    env = dict(os.environ)
    env['NO_COLOR'] = '1'
    result = subprocess.run(
        ['bash', str(INSTALLER_PATH), '--help'],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert '\x1b[' not in result.stdout
    assert '--status' in result.stdout
    assert '--no-auto-repair' in result.stdout
    assert '--uninstall' in result.stdout
    assert '--recovery-admin' in result.stdout
    assert '--recovery-password-file' in result.stdout
    assert '--non-interactive' in result.stdout
    assert '--yes, -y' in result.stdout
    assert 'Bez parametrów instalator uruchamia interaktywne menu wyboru operacji.' in result.stdout



def test_api_healthcheck_is_quiet_during_expected_startup_and_dumps_diagnostics_on_failure():
    assert '2> "$api_health_error"' in INSTALLER
    assert "systemctl is-active --quiet cloudportal-api.service" in INSTALLER
    assert "API startuje — oczekuję na port 127.0.0.1:8765" in INSTALLER
    assert "Ostatni błąd curl:" in INSTALLER
    assert "systemctl --no-pager --full status cloudportal-api.service" in INSTALLER
    assert "journalctl --no-pager -u cloudportal-api.service -n 80" in INSTALLER


def test_docker_compose_wires_updater_tokens_and_unix_socket():
    compose = (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
    nginx = (ROOT / 'scripts' / 'nginx-container.conf').read_text(encoding='utf-8')
    assert 'CP_UPDATER_UNIX_SOCKET: /run/cloudportal-updater/updater.sock' in compose
    assert 'CP_UPDATER_TOKEN_FILE: /run/cloudportal-updater-secrets/updater.token' in compose
    assert 'CP_UPDATER_STATUS_TOKEN_FILE: /run/cloudportal-updater-secrets/updater-status.token' in compose
    assert 'CP_UPDATER_HOST_CONFIG_DIR' in compose
    assert 'CP_UPDATER_RUNTIME_DIR' in compose
    assert 'host.docker.internal' not in compose
    assert 'location = /update-status' in nginx
    assert 'proxy_pass http://unix:/run/cloudportal-updater/updater.sock:/status$is_args$args;' in nginx


def test_docker_status_and_repair_include_updater_channel():
    assert "Updater: pliki tokenów są dostępne." in INSTALLER
    assert "Updater: cloudportal-updater.service aktywny." in INSTALLER
    assert "Updater: /update-status odpowiada przez reverse proxy." in INSTALLER
    assert "docker_prepare_updater_config ''" in INSTALLER
    assert 'docker_activate_updater "$release"' in INSTALLER
