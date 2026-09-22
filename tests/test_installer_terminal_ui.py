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
    assert 'Odinstaluj — zachowaj bazę i dane' in INSTALLER
    assert 'Odinstaluj całkowicie — usuń bazę i dane' in INSTALLER
    assert 'Odinstaluj Docker — zachowaj wolumeny i konfigurację' in INSTALLER
    assert 'Odinstaluj Docker całkowicie — usuń wolumeny i konfigurację' in INSTALLER
    assert "Wybierz operację [0-8]:" in INSTALLER


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


def test_installer_failure_trap_is_actionable_without_dumping_commands():
    assert 'installer_error()' in INSTALLER
    assert 'Etap „$CURRENT_STAGE” przerwany' in INSTALLER
    assert 'journalctl -u cloudportal-api' in INSTALLER
    assert '$BASH_COMMAND' not in INSTALLER


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
    assert '--uninstall' in result.stdout
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
