#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Keep the complete installer at the repository root so curl | bash is standalone.

ui_color=0
if [[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; then
  ui_color=1
fi
if ((ui_color)); then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_BLUE=$'\033[34m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
else
  C_RESET='' C_BOLD='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED=''
fi

CURRENT_STAGE='inicjalizacja'
ui_header() { printf '\n%b== %s ==%b\n' "$C_BOLD$C_BLUE" "$1" "$C_RESET"; }
ui_stage() {
  CURRENT_STAGE=$3
  printf '\n%b[%s/%s]%b %b%s%b\n' "$C_BLUE" "$1" "$2" "$C_RESET" "$C_BOLD" "$3" "$C_RESET"
}
ui_ok()   { printf '%b[ OK ]%b %s\n' "$C_GREEN" "$C_RESET" "$*"; }
ui_info() { printf '%b[INFO]%b %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ui_warn() { printf '%b[WARN]%b %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
ui_fail() { printf '%b[FAIL]%b %s\n' "$C_RED" "$C_RESET" "$*" >&2; }

usage() {
  cat <<'EOF'
Cloudportal-backed installer

Użycie:
  install.sh [opcje]

Bez parametrów instalator uruchamia interaktywne menu wyboru operacji.

Tryby:
  --status                    Pokaż stan instalacji i usług; w trybie Docker domyślnie spróbuj auto-naprawy.
  --no-auto-repair            Z --docker --status tylko diagnozuj; nie uruchamiaj ani nie odtwarzaj usług.
  --uninstall                 Odinstaluj Cloudportal; domyślnie zachowaj bazę, konfigurację i dane.
  --purge-data                Z --uninstall usuń także bazę, /etc, /var/lib i backupy.
  --yes, -y                   Pomiń potwierdzenie deinstalacji; wymagane bez TTY.
  --force-uninstall           Zgodnościowy alias: --uninstall --yes.
  --check-platform            Sprawdź obsługę systemu bez wykonywania instalacji.
  --gui, -gui                 Interaktywny interfejs dialog.
  --non-interactive           Tryb bez pytań; przy --uninstall wymaga także --yes.
  --docker                    Zainstaluj/obsłuż Cloudportal jako stack Docker Compose zamiast usług systemd.
  --recovery-admin            Recovery lokalnego administratora; alias: --recovery-password.
  --recovery-password         Utwórz lub odzyskaj lokalne konto Administrator bez reinstalacji.

Automatyczne aktualizacje (cron, istniejąca instalacja):
  --enable-auto-update        Włącz cron; domyślnie co 12 godzin. Nie reinstaluje aplikacji teraz.
  --disable-auto-update       Usuń wyłącznie harmonogram cron Cloudportal i jego helper.
  --auto-update-status        Pokaż harmonogram cron bez zmiany instalacji.
  --auto-update-interval N    Interwał z --enable-auto-update: 1,2,3,4,6,8,12,24 godziny.
                              Używa zapisanych ustawień updatera; --docker wybiera stack Docker.

Konfiguracja:
  --host HOST                 Host/DNS backendu.
  --port PORT                 Port HTTPS, domyślnie 8443.
  --workers N                 Liczba workerów 1-64.
  --enable-backups            Włącz codzienny backup PostgreSQL.
  --disable-backups           Wyłącz timer backupu.
  --backup-retention-days N   Retencja backupów, domyślnie 14 dni.
  --ref REF                   Branch/tag/commit GitHub, domyślnie main.
  --cert-file FILE            Własny certyfikat TLS.
  --cert-key FILE             Klucz do własnego certyfikatu TLS.
  --recovery-username USER    Login konta recovery; domyślnie recovery-admin.
  --recovery-email EMAIL      E-mail nowego konta; dla istniejącego opcjonalny.
  --recovery-project REF      Projekt docelowy: ID lub slug; domyślnie projekt Default.
  --recovery-password-file F  Plik 0600 z hasłem recovery; wymagany w trybie nieinteraktywnym.

GitHub:
  --github-token-file FILE    Plik 0600 z tokenem Contents: read.
  --github-config FILE        Plik curl 0600 z konfiguracją autoryzacji.

Blokada instalatora:
  --takeover                  Automatycznie przejmij aktywną instalację (domyślne).
  --no-takeover               Nie zatrzymuj poprzedniego instalatora.

Środowisko:
  NO_COLOR=1                  Wyłącz kolory ANSI nawet w terminalu.

Przykłady:
  sudo ./install.sh --non-interactive --port 8443
  sudo ./install.sh --docker --non-interactive --port 8443
  sudo ./install.sh --docker --status
  sudo ./install.sh --status
  sudo ./install.sh --enable-auto-update --auto-update-interval 12
  sudo ./install.sh --docker --enable-auto-update --auto-update-interval 12
  sudo ./install.sh --auto-update-status
  sudo ./install.sh --disable-auto-update
  sudo ./install.sh --recovery-admin
  sudo ./install.sh --docker --recovery-admin
  sudo ./install.sh --uninstall
  sudo ./install.sh --uninstall --purge-data
  sudo ./install.sh --non-interactive --uninstall --yes
EOF
}

installer_error() {
  local rc=$1 line=$2
  ui_fail "Etap „$CURRENT_STAGE” przerwany (kod $rc, linia $line)."
  ui_info 'Sprawdź komunikat bezpośrednio powyżej.'
  if [[ ${docker_mode:-0} == 1 ]]; then
    ui_info 'Docker: sudo docker ps --filter label=com.docker.compose.project=cloudportal-backed'
    ui_info 'Logi: uruchom sudo ./install.sh --docker --status, a następnie sudo docker compose logs.'
  else
    ui_info 'Usługi: systemctl status cloudportal-api cloudportal-dispatcher cloudportal-worker@1'
    ui_info 'Logi: journalctl -u cloudportal-api -u cloudportal-dispatcher -u cloudportal-worker@1 -n 100 --no-pager'
  fi
  exit "$rc"
}
trap 'rc=$?; installer_error "$rc" "$LINENO"' ERR

repo='chmajster/Cloudportal-backed'
initial_argc=$#
ref='main'
backend_host=''
backend_port=''
workers=''
github_token_file=''
github_config=''
cert_file=''
cert_key=''
backup_schedule=''
backup_retention_days=''
auto_update_action=''
auto_update_mode=0
auto_update_interval=12
auto_update_interval_explicit=0
gui=0
non_interactive=0
check_platform=0
takeover_running_install=1
purge_data=0
status_mode=0
uninstall_mode=0
recovery_mode=0
recovery_username=''
recovery_email=''
recovery_project=''
recovery_password_file=''
recovery_password=''
assume_yes=0
docker_mode=0
docker_auto_repair=1
docker_auto_repair_explicit=0
update_in_progress=${CLOUDPORTAL_UPDATE_IN_PROGRESS:-0}
[[ "$update_in_progress" == 1 ]] || update_in_progress=0
update_channel_ref=${CLOUDPORTAL_UPDATE_CHANNEL_REF:-}
install_progress() {
  local percent=$1 phase=$2 message=$3
  if ((update_in_progress)); then
    printf '::cloudportal-progress::%s::%s::%s\n' "$percent" "$phase" "$message"
  fi
}
while (($#)); do
  case "$1" in
    --host|--port|--workers|--ref|--github-token-file|--github-config|--cert-file|--cert-key|--backup-retention-days|--auto-update-interval|--recovery-username|--recovery-email|--recovery-project|--recovery-password-file)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --host) backend_host=$2;; --port) backend_port=$2;; --workers) workers=$2;; --ref) ref=$2;;
        --github-token-file) github_token_file=$2;; --github-config) github_config=$2;; --cert-file) cert_file=$2;; --cert-key) cert_key=$2;; --backup-retention-days) backup_retention_days=$2;;
        --auto-update-interval) auto_update_interval=$2; auto_update_interval_explicit=1;;
        --recovery-username) recovery_username=$2;; --recovery-email) recovery_email=$2;; --recovery-project) recovery_project=$2;; --recovery-password-file) recovery_password_file=$2;;
      esac
      shift 2;;
    --enable-auto-update|--disable-auto-update|--auto-update-status)
      ((auto_update_mode == 0)) || { ui_fail 'Wybierz tylko jedną operację auto-update.'; exit 2; }
      auto_update_mode=1
      case "$1" in
        --enable-auto-update) auto_update_action=enable;;
        --disable-auto-update) auto_update_action=disable;;
        --auto-update-status) auto_update_action=status;;
      esac
      shift;;
    --enable-backups) backup_schedule=true; shift;;
    --disable-backups) backup_schedule=false; shift;;
    --gui|-gui) gui=1; shift;;
    --non-interactive) non_interactive=1; shift;;
    --docker) docker_mode=1; shift;;
    --recovery-admin|--recovery-password) recovery_mode=1; shift;;
    --check-platform) check_platform=1; shift;;
    --takeover) takeover_running_install=1; shift;;
    --no-takeover) takeover_running_install=0; shift;;
    --uninstall) uninstall_mode=1; shift;;
    --force-uninstall) uninstall_mode=1; assume_yes=1; shift;;
    --yes|-y) assume_yes=1; shift;;
    --purge-data) purge_data=1; shift;;
    --status) status_mode=1; shift;;
    --no-auto-repair) docker_auto_repair=0; docker_auto_repair_explicit=1; shift;;
    --help|-h) usage; exit 0;;
    *) ui_fail "Nieznana opcja: $1"; ui_info 'Uruchom --help, aby zobaczyć dostępne opcje.'; exit 2;;
  esac
done

# Unattended/updater runs must never terminate a concurrent manual installer.
if ((update_in_progress)); then
  takeover_running_install=0
fi

interactive_action_menu() {
  ((initial_argc == 0)) || return 0

  if ! exec 3<>/dev/tty; then
    ui_fail 'Uruchomienie bez parametrów wymaga interaktywnego terminala.'
    ui_info 'W automatyzacji podaj jawny tryb, np. --non-interactive, --status albo --uninstall --yes.'
    exit 2
  fi

  ui_header 'Cloudportal-backed — wybór operacji'
  cat >&3 <<'EOF'
  [1] Instalacja / aktualizacja — systemd
  [2] Instalacja / aktualizacja — Docker
  [3] Status — systemd
  [4] Status / auto-naprawa — Docker
  [5] Odinstaluj — zachowaj bazę i dane
  [6] Odinstaluj całkowicie — usuń bazę i dane
  [7] Odinstaluj Docker — zachowaj wolumeny i konfigurację
  [8] Odinstaluj Docker całkowicie — usuń wolumeny i konfigurację
  [9] Recovery password / konto Administrator — systemd
  [10] Recovery password / konto Administrator — Docker
  [11] Włącz automatyczne aktualizacje cron — systemd
  [12] Włącz automatyczne aktualizacje cron — Docker
  [13] Wyłącz automatyczne aktualizacje cron
  [14] Status automatycznych aktualizacji cron
  [0] Wyjście
EOF

  local choice=''
  while :; do
    printf 'Wybierz operację [0-14]: ' >&3
    if ! IFS= read -r choice <&3; then
      exec 3>&-
      ui_fail 'Nie udało się odczytać wyboru z terminala.'
      exit 2
    fi
    case "$choice" in
      1)
        gui=1
        exec 3>&-
        return 0
        ;;
      2)
        docker_mode=1
        exec 3>&-
        return 0
        ;;
      3)
        status_mode=1
        exec 3>&-
        return 0
        ;;
      4)
        docker_mode=1
        status_mode=1
        exec 3>&-
        return 0
        ;;
      5)
        uninstall_mode=1
        exec 3>&-
        return 0
        ;;
      6)
        uninstall_mode=1
        purge_data=1
        exec 3>&-
        return 0
        ;;
      7)
        docker_mode=1
        uninstall_mode=1
        exec 3>&-
        return 0
        ;;
      8)
        docker_mode=1
        uninstall_mode=1
        purge_data=1
        exec 3>&-
        return 0
        ;;
      9)
        recovery_mode=1
        exec 3>&-
        return 0
        ;;
      10)
        docker_mode=1
        recovery_mode=1
        exec 3>&-
        return 0
        ;;
      11|12)
        auto_update_mode=1
        auto_update_action=enable
        [[ "$choice" != 12 ]] || docker_mode=1
        printf 'Interwał w godzinach [12] (1,2,3,4,6,8,12,24): ' >&3
        IFS= read -r auto_update_interval <&3 || { exec 3>&-; exit 2; }
        auto_update_interval=${auto_update_interval:-12}
        exec 3>&-
        return 0
        ;;
      13|14)
        auto_update_mode=1
        if [[ "$choice" == 13 ]]; then auto_update_action=disable; else auto_update_action=status; fi
        exec 3>&-
        return 0
        ;;
      0)
        exec 3>&-
        ui_info 'Nie wykonano żadnych zmian.'
        exit 0
        ;;
      *)
        ui_warn 'Nieprawidłowy wybór. Wpisz numer od 0 do 14.'
        ;;
    esac
  done
}

interactive_action_menu

mode_count=$((status_mode + uninstall_mode + check_platform + recovery_mode + auto_update_mode))
((mode_count <= 1)) || { ui_fail 'Wybierz tylko jeden tryb: --status, --uninstall, --check-platform albo operację --recovery-admin/auto-update.'; exit 2; }
((purge_data == 0 || uninstall_mode == 1)) || { ui_fail '--purge-data wymaga --uninstall.'; exit 2; }
((assume_yes == 0 || uninstall_mode == 1)) || { ui_fail '--yes/-y ma zastosowanie tylko z --uninstall.'; exit 2; }
((gui == 0 || uninstall_mode == 0)) || { ui_fail '--gui/-gui nie może być użyte razem z --uninstall.'; exit 2; }
((gui == 0 || recovery_mode == 0)) || { ui_fail '--gui/-gui nie jest obsługiwane w trybie recovery.'; exit 2; }
((gui == 0 || docker_mode == 0)) || { ui_fail '--gui/-gui nie jest obsługiwane w trybie --docker.'; exit 2; }
((docker_auto_repair_explicit == 0 || (docker_mode == 1 && status_mode == 1))) || { ui_fail '--no-auto-repair wymaga --docker --status.'; exit 2; }
((gui == 0 || auto_update_mode == 0)) || { ui_fail '--gui nie łączy się z zarządzaniem cron.'; exit 2; }
if ((auto_update_interval_explicit)) && [[ "$auto_update_action" != enable ]]; then
  ui_fail '--auto-update-interval wymaga --enable-auto-update.'
  exit 2
fi
if [[ "$auto_update_action" == enable ]]; then
  case "$auto_update_interval" in
    1|2|3|4|6|8|12|24) ;;
    *) ui_fail 'Interwał musi wynosić 1, 2, 3, 4, 6, 8, 12 albo 24 godziny.'; exit 2;;
  esac
fi
if ((recovery_mode == 0)); then
  [[ -z "$recovery_username" && -z "$recovery_email" && -z "$recovery_project" && -z "$recovery_password_file" ]] || {
    ui_fail 'Opcje --recovery-* wymagają --recovery-admin albo --recovery-password.'
    exit 2
  }
fi

drain_script_input() {
  [[ -t 0 ]] || cat >/dev/null || true
}
os_release_file=/etc/os-release
if ((check_platform)) && [[ -n ${CLOUDPORTAL_OS_RELEASE_FILE:-} ]]; then
  os_release_file=$CLOUDPORTAL_OS_RELEASE_FILE
fi
[[ -r "$os_release_file" ]] || { echo 'Unsupported operating system.' >&2; exit 1; }
. "$os_release_file"
case "$ID:$VERSION_ID" in
  ubuntu:24.04|ubuntu:26.04|debian:12|debian:13)
    os_family=debian
    python_command=python3
    key_value_package=redis-server
    key_value_command=redis-server
    ;;
  rhel:9|rhel:9.*)
    os_family=rhel
    rhel_major=9
    python_command=python3.12
    key_value_package=redis
    key_value_command=redis-server
    ;;
  rhel:10|rhel:10.*)
    os_family=rhel
    rhel_major=10
    python_command=python3
    key_value_package=valkey
    key_value_command=valkey-server
    ;;
  *)
    if ((uninstall_mode || status_mode || docker_mode || recovery_mode || auto_update_mode)); then
      os_family=unknown
      python_command=python3
      key_value_package=unknown
      key_value_command=unknown
      ui_warn "System $ID $VERSION_ID nie jest wspierany do instalacji; tryb status/deinstalacji będzie kontynuowany."
    else
      echo 'Supported: Ubuntu 24.04/26.04, Debian 12/13, RHEL 9/10 with systemd.' >&2
      drain_script_input
      exit 1
    fi
    ;;
esac
case "$(uname -m)" in
  x86_64) arch=amd64;;
  aarch64|arm64) arch=arm64;;
  *)
    if ((uninstall_mode || status_mode || recovery_mode || auto_update_mode)); then
      arch=$(uname -m)
      ui_warn "Architektura $arch nie jest wspierana do instalacji; tryb status/deinstalacji będzie kontynuowany."
    else
      ui_fail "Nieobsługiwana architektura: $(uname -m). Obsługiwane: amd64/arm64."
      exit 1
    fi
    ;;
esac
if ((check_platform)); then
  ui_header 'Pretest platformy'
  ui_ok "Obsługiwany system: $NAME $VERSION_ID"
  ui_info "Rodzina: $os_family · architektura: $arch · KV: $key_value_package · Python: $python_command"
  drain_script_input
  exit 0
fi
if [[ "$auto_update_action" != status ]] && ((status_mode == 0 || (docker_mode == 1 && status_mode == 1 && docker_auto_repair == 1))); then
  [[ $EUID -eq 0 ]] || {
    if ((docker_mode == 1 && status_mode == 1)); then
      ui_fail 'Auto-naprawa Docker wymaga roota. Uruchom przez sudo albo użyj --no-auto-repair.'
    else
      ui_fail 'Instalacja, deinstalacja i recovery wymagają roota. Uruchom przez sudo bash.'
    fi
    exit 1
  }
fi

recovery_prepare_inputs() {
  CURRENT_STAGE='dane recovery administratora'

  if [[ -z "$recovery_username" ]]; then
    if ((non_interactive)); then
      recovery_username='recovery-admin'
    else
      [[ -r /dev/tty && -w /dev/tty ]] || { ui_fail 'Recovery bez --recovery-password-file wymaga interaktywnego terminala.'; return 2; }
      printf 'Login konta recovery [recovery-admin]: ' >/dev/tty
      IFS= read -r recovery_username </dev/tty || true
      recovery_username=${recovery_username:-recovery-admin}
    fi
  fi
  recovery_username=${recovery_username,,}
  [[ "$recovery_username" =~ ^[a-z0-9][a-z0-9_.-]{0,62}$ ]] || {
    ui_fail 'Login recovery musi mieć 1-63 znaki: litery/cyfry oraz . _ -.'
    return 2
  }

  if [[ -z "$recovery_email" && $non_interactive -eq 0 ]]; then
    printf 'E-mail konta (Enter = zachowaj istniejący / dla nowego użyj login@localhost.example): ' >/dev/tty
    IFS= read -r recovery_email </dev/tty || true
  fi
  recovery_email=${recovery_email,,}
  if [[ -n "$recovery_email" ]]; then
    [[ ${#recovery_email} -le 254 && "$recovery_email" == *@* && "$recovery_email" != *[[:space:]]* ]] || {
      ui_fail 'Nieprawidłowy e-mail recovery.'
      return 2
    }
  fi

  if [[ -z "$recovery_project" && $non_interactive -eq 0 ]]; then
    printf 'Projekt docelowy — ID lub slug [default]: ' >/dev/tty
    IFS= read -r recovery_project </dev/tty || true
  fi
  [[ ${#recovery_project} -le 100 ]] || { ui_fail 'Identyfikator projektu recovery jest za długi.'; return 2; }

  if [[ -n "$recovery_password_file" ]]; then
    [[ -f "$recovery_password_file" && ! -L "$recovery_password_file" && -r "$recovery_password_file" ]] || {
      ui_fail 'Plik --recovery-password-file musi być zwykłym, czytelnym plikiem.'
      return 2
    }
    [[ "$(stat -c %a "$recovery_password_file" 2>/dev/null || true)" == 600 ]] || {
      ui_fail 'Plik --recovery-password-file musi mieć tryb 0600.'
      return 2
    }
    recovery_password=$(head -n 1 "$recovery_password_file")
  else
    ((non_interactive == 0)) || {
      ui_fail 'Tryb --non-interactive recovery wymaga --recovery-password-file FILE.'
      return 2
    }
    [[ -r /dev/tty && -w /dev/tty ]] || { ui_fail 'Recovery hasła wymaga /dev/tty albo --recovery-password-file.'; return 2; }
    local confirmation=''
    printf 'Nowe hasło administratora: ' >/dev/tty
    IFS= read -r -s recovery_password </dev/tty || true
    printf '\nPowtórz hasło: ' >/dev/tty
    IFS= read -r -s confirmation </dev/tty || true
    printf '\n' >/dev/tty
    if [[ "$recovery_password" != "$confirmation" ]]; then
      unset confirmation recovery_password
      recovery_password=''
      ui_fail 'Podane hasła nie są identyczne.'
      return 2
    fi
    unset confirmation
  fi

  if ((${#recovery_password} < 12 || ${#recovery_password} > 256)); then
    unset recovery_password
    recovery_password=''
    ui_fail 'Hasło recovery musi mieć od 12 do 256 znaków.'
    return 2
  fi
}

# Cron is opt-in and only submits work to the existing privileged updater.
# Keep this block inline: install.sh must also work when downloaded on its own.
auto_update_valid_interval() {
  case "$1" in 1|2|3|4|6|8|12|24) return 0;; *) return 1;; esac
}

auto_update_cron_contents() {
  local hours=$1 schedule
  auto_update_valid_interval "$hours" || return 2
  if [[ "$hours" == 24 ]]; then
    schedule='0 0 * * *'
  else
    schedule="0 */$hours * * *"
  fi
  printf '%s\n' '# Managed by Cloudportal install.sh; do not edit.' \
    'SHELL=/bin/sh' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    "$schedule root /usr/local/sbin/cloudportal-auto-update >> /var/log/cloudportal-auto-update.log 2>&1"
}

auto_update_client_contents() {
  cat <<'PY_CRON_UPDATE'
#!/usr/bin/env python3
"""Cron client: reuse updater authentication, CI gates, backups and deduplication."""
import argparse
import fcntl
import http.client
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=15)
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def report(level, message):
    print("{} [{}] {}".format(datetime.now(timezone.utc).isoformat(), level, message), flush=True)


def request_updater(docker, path, payload):
    config = Path("/etc/cloudportal-backed-docker" if docker else "/etc/cloudportal-backed")
    token = (config / "updater.token").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,512}", token):
        raise ValueError("Nieprawidlowy updater.token; uruchom instalator, aby naprawic updater.")
    connection = (UnixHTTPConnection("/run/cloudportal-updater-docker/updater.sock")
                  if docker else http.client.HTTPConnection("127.0.0.1", 8766, timeout=15))
    try:
        connection.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers={
            "Content-Type": "application/json", "X-Updater-Token": token,
        })
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("Odpowiedz updatera jest zbyt duza.")
        if response.status not in (200, 202):
            # Do not log response bodies: they can contain private updater details.
            raise ValueError("Updater zwrocil HTTP {}; sprawdz cloudportal-updater.service.".format(response.status))
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Updater zwrocil nieprawidlowy format odpowiedzi.")
        return result
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--configure", action="store_true", help="Disable the built-in scheduler; cron owns the schedule.")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        report("FAIL", "Uruchom przez sudo; token updatera jest dostepny tylko dla administratora.")
        return 1
    root = Path("/opt/cloudportal-backed-docker" if args.docker else "/opt/cloudportal-backed")
    if not (root / "current" / ".cloudportal-release.json").is_file():
        report("WARN", "Brak aktywnej instalacji; nie uruchamiam instalacji od nowa.")
        return 1 if args.configure else 0
    try:
        if args.configure:
            result = request_updater(args.docker, "/settings", {"enabled": False})
            if result.get("enabled") is not False:
                raise ValueError("Updater nie potwierdzil wylaczenia wewnetrznego harmonogramu.")
            report(" OK ", "Wewnetrzny harmonogram updatera wylaczony; harmonogramem zarzadza cron.")
            return 0
        # Probe only. The installer takes this lock itself; never hold it across /run.
        # CLOUDPORTAL_UPDATE_IN_PROGRESS makes install.sh refuse lock takeover.
        with open("/run/cloudportal-install.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report("INFO", "Instalator jest zajety; pomijam ten termin aktualizacji.")
                return 0
            fcntl.flock(lock, fcntl.LOCK_UN)
        result = request_updater(args.docker, "/run", {})
        if result.get("already_running") is True:
            report("INFO", "Aktualizacja juz trwa; nie uruchamiam drugiej.")
        elif result.get("accepted") is True:
            report(" OK ", "Zlecono sprawdzenie i aktualizacje. Wynik: panel aktualizacji / cloudportal-updater.service.")
        else:
            raise ValueError("Updater nie potwierdzil przyjecia zadania.")
        return 0
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Exception text can contain response data or paths; expose only its class.
        report("FAIL", "Nie udalo sie wywolac updatera ({}). Sprawdz cloudportal-updater.service i jego konfiguracje.".format(type(exc).__name__))
        return 1


if __name__ == "__main__":
    sys.exit(main())
PY_CRON_UPDATE
}

auto_update_show_status() {
  ui_header 'Automatyczne aktualizacje — cron'
  if [[ -f /etc/cron.d/cloudportal-auto-update ]]; then
    ui_ok 'Harmonogram cron jest skonfigurowany.'
    awk '$1 !~ /^#/ && $6 == "root" {print "[INFO] Cron: " $1 " " $2 " " $3 " " $4 " " $5}' /etc/cron.d/cloudportal-auto-update
    if systemctl is-active --quiet cron.service 2>/dev/null || systemctl is-active --quiet crond.service 2>/dev/null; then
      ui_ok 'Usługa cron jest aktywna.'
    else
      ui_warn 'Usługa cron nie jest aktywna; harmonogram nie będzie wykonywany.'
    fi
    [[ -f /usr/local/sbin/cloudportal-auto-update && -f /usr/local/lib/cloudportal-updater/cron-update.py ]] || ui_warn 'Brakuje helpera cron; ponownie uruchom --enable-auto-update.'
    ui_info 'Log zleceń: /var/log/cloudportal-auto-update.log'
    ui_info 'Wynik aktualizacji: panel aktualizacji; journalctl -u cloudportal-updater.service'
  else
    ui_info 'Harmonogram cron jest wyłączony.'
  fi
}

auto_update_remove() {
  rm -f /etc/cron.d/cloudportal-auto-update \
    /usr/local/sbin/cloudportal-auto-update \
    /usr/local/lib/cloudportal-updater/cron-update.py \
    /etc/logrotate.d/cloudportal-auto-update
  ui_ok 'Usunięto harmonogram i helper cron Cloudportal; pozostałe zadania cron są bez zmian.'
  ui_info 'Historia logów została zachowana. Wewnętrzny harmonogram updatera nie jest automatycznie włączany.'
}

auto_update_manage() {
  case "$auto_update_action" in
    status) auto_update_show_status; return 0;;
    disable) auto_update_remove; return 0;;
  esac

  ui_header 'Cloudportal-backed — konfiguracja auto-update cron'
  ui_stage 1 3 'Pretest harmonogramu'
  auto_update_valid_interval "$auto_update_interval" || {
    ui_fail 'Interwał musi wynosić 1, 2, 3, 4, 6, 8, 12 albo 24 godziny.'
    return 2
  }
  local root=/opt/cloudportal-backed config_dir=/etc/cloudportal-backed
  local cron_unit=cron.service cron_package=cron python_bin work
  local client_args=()
  if ((docker_mode)); then
    root=/opt/cloudportal-backed-docker
    config_dir=/etc/cloudportal-backed-docker
    client_args+=(--docker)
  fi
  [[ -f "$root/current/.cloudportal-release.json" && -s "$config_dir/updater.token" ]] || {
    ui_fail 'Brak kompletnej instalacji. Najpierw wykonaj instalację/aktualizację bez --enable-auto-update.'
    return 1
  }
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]] || {
    ui_fail 'Konfiguracja cron wymaga aktywnego systemd na hoście.'
    return 1
  }
  systemctl is-active --quiet cloudportal-updater.service || {
    ui_fail 'Updater nie działa. Sprawdź: systemctl status cloudportal-updater.service'
    return 1
  }
  python_bin=$(command -v "$python_command") || {
    ui_fail "Brak wymaganego interpretera: $python_command"
    return 1
  }
  case "$os_family" in
    debian) ;;
    rhel) cron_unit=crond.service; cron_package=cronie;;
    *) ui_fail 'Automatyczna konfiguracja cron obsługuje Debian/Ubuntu i RHEL.'; return 1;;
  esac
  local path
  for path in /etc/cron.d/cloudportal-auto-update /etc/logrotate.d/cloudportal-auto-update \
      /usr/local/sbin/cloudportal-auto-update /usr/local/lib/cloudportal-updater/cron-update.py \
      /var/log/cloudportal-auto-update.log; do
    [[ ! -L "$path" ]] || { ui_fail "Odmowa zapisu przez symlink: $path"; return 1; }
  done

  ui_stage 2 3 'Usługa cron i helper aktualizacji'
  if ! command -v crontab >/dev/null 2>&1 || ! command -v logrotate >/dev/null 2>&1; then
    case "$os_family" in
      debian) DEBIAN_FRONTEND=noninteractive apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y "$cron_package" logrotate;;
      rhel) dnf install -y "$cron_package" logrotate;;
    esac
  fi
  systemctl enable --now "$cron_unit"
  systemctl is-active --quiet "$cron_unit" || { ui_fail "Usługa $cron_unit nie wystartowała."; return 1; }
  install -d -m 0755 /etc/cron.d /etc/logrotate.d /usr/local/sbin /usr/local/lib/cloudportal-updater
  work=$(mktemp -d)
  auto_update_client_contents > "$work/cron-update.py"
  "$python_bin" - "$work/cron-update.py" <<'PY_VALIDATE_CRON'
import ast, sys
from pathlib import Path
ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8'))
PY_VALIDATE_CRON
  printf '#!/usr/bin/env bash\nset -Eeuo pipefail\numask 077\nexport NO_COLOR=1\nexec %q /usr/local/lib/cloudportal-updater/cron-update.py' "$python_bin" > "$work/runner"
  ((docker_mode == 0)) || printf ' --docker' >> "$work/runner"
  printf '\n' >> "$work/runner"
  auto_update_cron_contents "$auto_update_interval" > "$work/cron"
  bash -n "$work/runner"
  install -m 0700 -o root -g root "$work/cron-update.py" /usr/local/lib/cloudportal-updater/cron-update.py
  install -m 0750 -o root -g root "$work/runner" /usr/local/sbin/cloudportal-auto-update
  touch /var/log/cloudportal-auto-update.log
  chown root:root /var/log/cloudportal-auto-update.log
  chmod 0600 /var/log/cloudportal-auto-update.log
  cat > /etc/logrotate.d/cloudportal-auto-update <<'CRON_LOGROTATE'
/var/log/cloudportal-auto-update.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 0600 root root
}
CRON_LOGROTATE
  chmod 0644 /etc/logrotate.d/cloudportal-auto-update
  chown root:root /etc/logrotate.d/cloudportal-auto-update

  ui_stage 3 3 'Aktywacja harmonogramu'
  local pending
  pending=$(mktemp /etc/cron.d/.cloudportal-auto-update.XXXXXX)
  install -m 0644 -o root -g root "$work/cron" "$pending"
  # Keep the service running. Only its internal scheduling loop is disabled.
  # /settings preserves ref, GitHub credentials and all update safety gates.
  if ! "$python_bin" "$work/cron-update.py" "${client_args[@]}" --configure; then
    rm -f "$pending"
    rm -rf "$work"
    ui_fail 'Nie udało się skonfigurować updatera; nowy harmonogram cron nie został aktywowany.'
    return 1
  fi
  mv -f "$pending" /etc/cron.d/cloudportal-auto-update
  rm -rf "$work"
  ui_ok "Automatyczne aktualizacje przez cron: co $auto_update_interval godzin, według czasu serwera."
  ui_info 'Domyślne 12 godzin oznacza 00:00 i 12:00. Aktualizacja nie jest uruchamiana teraz.'
  ui_info 'Kanał, dane GitHub, backup i weryfikacja CI: istniejące ustawienia updatera.'
  auto_update_show_status
}

docker_root=/opt/cloudportal-backed-docker
docker_config=/etc/cloudportal-backed-docker
docker_env="$docker_config/docker.env"
docker_pending_env="$docker_config/docker.env.pending"
docker_tls="$docker_config/tls"
docker_updater_data=/var/lib/cloudportal-backed-docker
docker_updater_runtime=/run/cloudportal-updater-docker
docker_project=cloudportal-backed
DOCKER_COMPOSE=()

docker_valid_host() {
  [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$ ]]
}

docker_valid_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535 && 10#$1 != 6389 && 10#$1 != 8765 && 10#$1 != 8766))
}

docker_valid_workers() {
  [[ "$1" =~ ^[0-9]{1,2}$ ]] && ((10#$1 >= 1 && 10#$1 <= 64))
}

DOCKER_INSTALL_LOCK_FD=''

docker_acquire_install_lock() {
  command -v flock >/dev/null 2>&1 || {
    ui_fail 'Brak komendy flock wymaganej do bezpiecznej instalacji Docker.'
    exit 1
  }

  exec {DOCKER_INSTALL_LOCK_FD}>/run/cloudportal-install.lock
  if flock --exclusive --nonblock "$DOCKER_INSTALL_LOCK_FD"; then
    ui_ok 'Blokada instalatora Docker przejęta.'
    return 0
  fi

  if ((takeover_running_install == 0)); then
    ui_fail 'Inna instalacja Cloudportal jest aktywna, a --no-takeover zabrania oczekiwania na blokadę.'
    exit 1
  fi

  ui_warn 'Inna instalacja Cloudportal jest aktywna; oczekuję na bezpieczne zwolnienie blokady.'
  if flock --exclusive --wait 120 "$DOCKER_INSTALL_LOCK_FD"; then
    ui_ok 'Blokada instalatora Docker przejęta po oczekiwaniu.'
    return 0
  fi

  ui_fail 'Nie udało się uzyskać /run/cloudportal-install.lock w ciągu 120 sekund.'
  exit 1
}

docker_preflight() {
  local failed=0 command free_kib auth_tmp docker_root_dir docker_root_kib registry_code registry_auth_code
  ui_info "System: $NAME $VERSION_ID · $arch · tryb Docker"
  ui_info "Cel: https://$backend_host:$backend_port · workery: $workers · ref: $ref"

  for command in awk sed grep tar openssl curl df sha256sum stat hostname flock ss "$python_command" systemctl; do
    if ! command -v "$command" >/dev/null 2>&1; then
      ui_fail "Brak wymaganej komendy przed instalacją Docker: $command"
      failed=1
    fi
  done
  if [[ ! -d /run/systemd/system ]]; then
    ui_fail 'Tryb Docker wymaga aktywnego systemd na hoście dla niezależnego serwisu aktualizacji.'
    failed=1
  fi
  ((failed == 0)) || {
    ui_info 'Uzupełnij brakujące narzędzia bazowe przed uruchomieniem instalatora; preflight nie modyfikuje systemu.'
    return 1
  }

  free_kib=$(df -Pk / 2>/dev/null | awk 'NR==2 {print $4}')
  if [[ "$free_kib" =~ ^[0-9]+$ ]]; then
    if ((free_kib < 2097152)); then
      ui_fail "Za mało wolnego miejsca na /: $((free_kib / 1024)) MiB. Wymagane minimum 2 GiB, zalecane 10 GiB."
      failed=1
    elif ((free_kib < 10485760)); then
      ui_warn "Wolne miejsce na /: $((free_kib / 1024)) MiB. Zalecane co najmniej 10 GiB."
    else
      ui_ok "Wolne miejsce na /: $((free_kib / 1024 / 1024)) GiB"
    fi
  else
    ui_fail 'Nie udało się ustalić wolnego miejsca na /.'
    failed=1
  fi

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker_root_dir=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
    if [[ -n "$docker_root_dir" ]]; then
      docker_root_kib=$(df -Pk "$docker_root_dir" 2>/dev/null | awk 'NR==2 {print $4}')
      if [[ "$docker_root_kib" =~ ^[0-9]+$ ]]; then
        if ((docker_root_kib < 2097152)); then
          ui_fail "Za mało wolnego miejsca w DockerRootDir ($docker_root_dir): $((docker_root_kib / 1024)) MiB."
          failed=1
        elif ((docker_root_kib < 10485760)); then
          ui_warn "DockerRootDir $docker_root_dir ma tylko $((docker_root_kib / 1024)) MiB wolnego miejsca."
        else
          ui_ok "DockerRootDir $docker_root_dir: $((docker_root_kib / 1024 / 1024)) GiB wolnego"
        fi
      else
        ui_fail "Nie udało się sprawdzić wolnego miejsca dla DockerRootDir: $docker_root_dir"
        failed=1
      fi
    fi
  fi

  auth_tmp=$(mktemp -d)
  docker_prepare_github_curl "$auth_tmp"
  if curl "${DOCKER_CURL_ARGS[@]}" --connect-timeout 5 --max-time 10 \
      "https://api.github.com/repos/$repo/contents/install.sh?ref=$ref" >/dev/null 2>&1; then
    ui_ok "Dostęp do źródła GitHub: $repo @ $ref"
  else
    ui_fail "Nie można odczytać $repo @ $ref z wybraną konfiguracją GitHub. Sprawdź --ref, token/config, DNS i proxy/firewall."
    failed=1
  fi
  rm -rf "$auth_tmp"

  registry_code=$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' https://registry-1.docker.io/v2/ 2>/dev/null || true)
  if [[ "$registry_code" == 200 || "$registry_code" == 401 ]]; then
    ui_ok 'Połączenie z Docker Hub registry'
  else
    ui_fail "Brak połączenia z registry-1.docker.io (HTTP: ${registry_code:-brak}). Dockerfile i Compose wymagają obrazów z Docker Hub."
    failed=1
  fi

  registry_auth_code=$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' 'https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/alpine:pull' 2>/dev/null || true)
  if [[ "$registry_auth_code" == 200 ]]; then
    ui_ok 'Połączenie z Docker Hub auth'
  else
    ui_fail "Brak połączenia z auth.docker.io (HTTP: ${registry_auth_code:-brak})."
    failed=1
  fi

  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$backend_port$"; then
    if command -v docker >/dev/null 2>&1 && docker ps         --filter "label=com.docker.compose.project=$docker_project"         --filter "label=com.docker.compose.service=proxy"         --format '{{.Ports}}' 2>/dev/null | grep -Eq "(^|:)$backend_port->"; then
      ui_info "Port $backend_port jest używany przez istniejący proxy Cloudportal; reinstalacja może go przejąć."
    else
      ui_fail "Port $backend_port jest już zajęty przez proces lub kontener spoza projektu $docker_project."
      failed=1
    fi
  else
    ui_ok "Port $backend_port jest dostępny."
  fi

  ((failed == 0))
}

docker_certificate_key_matches() {
  local cert_public key_public
  cert_public=$(openssl x509 -in "$1" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256 2>/dev/null) || return 1
  key_public=$(openssl pkey -in "$2" -pubout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256 2>/dev/null) || return 1
  [[ -n "$cert_public" && "$cert_public" == "$key_public" ]]
}

docker_certificate_matches_host() {
  if [[ "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    openssl x509 -in "$1" -noout -checkip "$backend_host" >/dev/null 2>&1
  else
    openssl x509 -in "$1" -noout -checkhost "$backend_host" >/dev/null 2>&1
  fi
}

docker_certificate_is_self_signed() {
  local subject issuer
  subject=$(openssl x509 -in "$1" -noout -subject -nameopt RFC2253 2>/dev/null) || return 1
  issuer=$(openssl x509 -in "$1" -noout -issuer -nameopt RFC2253 2>/dev/null) || return 1
  [[ "${subject#subject=}" == "${issuer#issuer=}" ]]
}

docker_generate_managed_tls() {
  local target_dir=$1 san="DNS:$backend_host"
  install -d -m 0700 "$target_dir"
  [[ ! "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || san="IP:$backend_host"
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -keyout "$target_dir/server.key" -out "$target_dir/server.crt" \
    -subj "/CN=$backend_host" -addext "subjectAltName=$san" >/dev/null 2>&1
  chmod 0600 "$target_dir/server.crt" "$target_dir/server.key"
  printf '%s\n' "$backend_host" > "$target_dir/host"
  printf '%s\n' managed-self-signed > "$target_dir/source"
}

docker_compose_detect() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker compose)
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker-compose)
    return 0
  fi
  return 1
}

docker_install_dependencies() {
  local need_install=0
  command -v docker >/dev/null 2>&1 || need_install=1
  docker_compose_detect || need_install=1

  if ((need_install)); then
    ui_info 'Docker Engine/Compose nie jest kompletny; instaluję runtime po zakończonym preflight.'
    case "$os_family" in
    debian)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y ca-certificates docker.io
      if ! docker compose version >/dev/null 2>&1; then
        if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
          apt-get install -y docker-compose-v2
        elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
          apt-get install -y docker-compose-plugin
        else
          apt-get install -y docker-compose
        fi
      fi
      ;;
    rhel)
      if ! command -v docker >/dev/null 2>&1; then
        ui_fail 'Na RHEL zainstaluj Docker Engine oraz Docker Compose plugin zgodnie z polityką serwera, następnie uruchom instalator ponownie z --docker.'
        exit 1
      fi
      ;;
      *)
        ui_fail 'Brak Docker Engine/Compose. Na tym systemie instalator nie instaluje runtime Docker automatycznie.'
        ui_info 'Zainstaluj Docker Engine i Docker Compose, potem uruchom ponownie install.sh --docker.'
        exit 1
        ;;
    esac
  fi

  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    systemctl enable --now docker >/dev/null 2>&1 || true
  fi
  docker_compose_detect || {
    ui_fail 'Docker Compose nie jest dostępny po instalacji zależności.'
    ui_info 'Wymagane jest polecenie docker compose albo docker-compose.'
    exit 1
  }
  docker info >/dev/null 2>&1 || {
    ui_fail 'Docker Engine nie odpowiada.'
    ui_info 'Sprawdź: systemctl status docker oraz docker info'
    exit 1
  }
}

docker_current_release() {
  readlink -f "$docker_root/current" 2>/dev/null || true
}

docker_compose_for() {
  local release=$1 env_file=$2
  shift 2
  [[ -n "$release" && -f "$release/docker-compose.yml" ]] || {
    ui_fail "Brak pliku Compose w release: ${release:-?}"
    return 1
  }
  [[ -r "$env_file" ]] || {
    ui_fail "Brak konfiguracji Docker: $env_file"
    return 1
  }
  "${DOCKER_COMPOSE[@]}" -p "$docker_project" --env-file "$env_file" -f "$release/docker-compose.yml" "$@"
}

docker_compose() {
  local release
  release=$(docker_current_release)
  [[ -n "$release" ]] || {
    ui_fail "Brak aktywnego release Docker w $docker_root/current."
    return 1
  }
  docker_compose_for "$release" "$docker_env" "$@"
}

docker_prepare_updater_config() {
  local channel_ref=${1:-}
  local token_file persistent_github_token='' persistent_github_config='' updater_config

  install -d -m 0700 "$docker_config" "$docker_updater_data" "$docker_updater_data/update"
  install -d -m 0755 "$docker_updater_runtime"
  for token_file in "$docker_config/updater.token" "$docker_config/updater-status.token"; do
    if [[ ! -s "$token_file" ]]; then
      openssl rand -hex 32 > "$token_file"
    fi
    # Katalog hosta ma 0700; sam plik musi być czytelny dla UID 10001 po bind-mount do kontenera API.
    chmod 0644 "$token_file"
    chown root:root "$token_file"
  done

  if [[ -n "$github_token_file" ]]; then
    persistent_github_token="$docker_config/github.token"
    if [[ "$github_token_file" != "$persistent_github_token" ]]; then
      install -m 0600 "$github_token_file" "$persistent_github_token"
    fi
    chown root:root "$persistent_github_token"
  fi
  if [[ -n "$github_config" ]]; then
    persistent_github_config="$docker_config/github.curl.conf"
    if [[ "$github_config" != "$persistent_github_config" ]]; then
      install -m 0600 "$github_config" "$persistent_github_config"
    fi
    chown root:root "$persistent_github_config"
  fi

  updater_config="$docker_config/updater.json"
  "$python_command" - "$updater_config" "$channel_ref" "$persistent_github_token" "$persistent_github_config" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text()) if path.exists() else {}
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
data.setdefault('enabled', False)
data.setdefault('interval_hours', 24)
data.setdefault('ref', 'main')
data.setdefault('github_token_file', '')
data.setdefault('github_config', '')
data.setdefault('require_ci', True)
data.setdefault('ci_workflow', 'Backend CI')
data.setdefault('ci_wait_minutes', 45)
data.setdefault('candidate_validation', True)
data.setdefault('runtime_preflight', True)
if sys.argv[2]:
    data['ref'] = sys.argv[2]
if sys.argv[3]:
    data['github_token_file'] = sys.argv[3]
if sys.argv[4]:
    data['github_config'] = sys.argv[4]
path.write_text(json.dumps(data, indent=2) + '\n')
os.chmod(path, 0o600)
PY
  chown root:root "$updater_config"
}

docker_activate_updater() {
  local release=$1
  [[ -f "$release/scripts/update-service.py" ]] || {
    ui_fail "Release nie zawiera scripts/update-service.py: $release"
    return 1
  }

  install -d -m 0755 /usr/local/lib/cloudportal-updater
  install -m 0755 "$release/scripts/update-service.py" /usr/local/lib/cloudportal-updater/update-service.py

  cat > /etc/systemd/system/cloudportal-updater.service <<EOF
[Unit]
Description=Cloudportal independent auto-update service (Docker)
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=simple
ExecStart=$python_command /usr/local/lib/cloudportal-updater/update-service.py
Environment=CP_UPDATER_REPOSITORY=$repo
# Docker updater is Unix-socket-only; it does not reserve host TCP port 8766.
Environment=CP_UPDATER_SOCKET=$docker_updater_runtime/updater.sock
Environment=CP_UPDATER_INSTALL_MODE=docker
Environment=CP_UPDATER_CONFIG_DIR=$docker_config
Environment=CP_UPDATER_DATA_DIR=$docker_updater_data
Environment=CP_UPDATER_APP_ROOT=$docker_root
Restart=always
RestartSec=3
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable cloudportal-updater.service >/dev/null
  if ((update_in_progress)); then
    systemctl is-active --quiet cloudportal-updater.service || systemctl start cloudportal-updater.service
  else
    systemctl restart cloudportal-updater.service
  fi
}

docker_updater_status_probe() {
  local public_port=${1:-8443} token=''
  [[ -s "$docker_config/updater-status.token" ]] || return 1
  token=$(tr -d '\r\n' < "$docker_config/updater-status.token")
  [[ -n "$token" ]] || return 1
  curl -kfsS --connect-timeout 2 --max-time 8 \
    -H "X-Update-Status-Token: $token" \
    "https://127.0.0.1:$public_port/update-status" >/dev/null
}

docker_rollback_candidate() {
  local candidate_release=$1 candidate_env=$2 previous_release=$3 previous_workers=$4
  local tls_changed=$5 tls_backup_dir=$6 had_previous_tls=$7

  ui_warn 'Przywracam ostatni aktywny stan Docker po nieudanej walidacji kandydata.'

  if ((tls_changed)); then
    rm -rf "$docker_tls"
    install -d -m 0700 "$docker_tls"
    if ((had_previous_tls)); then
      cp -a "$tls_backup_dir/." "$docker_tls/"
      ui_info 'Przywrócono poprzedni materiał TLS.'
    fi
  fi

  if [[ -n "$previous_release" && -f "$previous_release/docker-compose.yml" && -r "$docker_env" ]]; then
    docker_compose_for "$previous_release" "$docker_env" up -d --remove-orphans --scale "worker=${previous_workers:-1}" || true
    if ((tls_changed)); then
      docker_compose_for "$previous_release" "$docker_env" restart proxy || true
    fi
  else
    docker_compose_for "$candidate_release" "$candidate_env" down --remove-orphans >/dev/null 2>&1 || true
  fi
}

docker_status_check() {
  CURRENT_STAGE='status Docker'
  ui_header 'Cloudportal-backed — status Docker'
  auto_update_show_status

  local failed=0
  local release public_port='8443' public_host='' expected_workers='1'
  local required_services=(postgres redis migrate api worker dispatcher proxy)
  local long_running_services=(postgres redis api dispatcher proxy)
  local compose_services=()
  local ids=()
  local service state health exit_code
  local running_workers=0 total_workers=0

  docker_compose_detect || {
    ui_fail 'Docker Compose nie jest dostępny.'
    return 1
  }
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || {
    ui_fail 'Docker Engine nie odpowiada.'
    ui_info 'Sprawdź: systemctl status docker oraz docker info'
    return 1
  }

  release=$(docker_current_release)
  if [[ -n "$release" && -d "$release" ]]; then
    ui_ok "Runtime Docker: $release"
  else
    ui_fail "Brak aktywnego release Docker w $docker_root/current."
    failed=1
  fi

  if [[ -r "$docker_env" ]]; then
    public_host=$(sed -n 's/^CP_PUBLIC_HOST=//p' "$docker_env" | tail -n 1)
    public_port=$(sed -n 's/^CP_HTTPS_PORT=//p' "$docker_env" | tail -n 1)
    expected_workers=$(sed -n 's/^CP_WORKER_COUNT=//p' "$docker_env" | tail -n 1)
    public_port=${public_port:-8443}
    expected_workers=${expected_workers:-1}
    ui_info "Endpoint HTTPS: https://${public_host:-localhost}:$public_port"
    ui_info "Oczekiwana liczba workerów: $expected_workers"
  else
    ui_fail "Brak konfiguracji Docker: $docker_env"
    failed=1
  fi

  if [[ -s "$docker_config/updater.token" && -s "$docker_config/updater-status.token" ]]; then
    ui_ok 'Updater: pliki tokenów są dostępne.'
  else
    ui_fail 'Updater: brakuje updater.token lub updater-status.token.'
    failed=1
  fi
  if systemctl is-active --quiet cloudportal-updater.service 2>/dev/null; then
    ui_ok 'Updater: cloudportal-updater.service aktywny.'
    if [[ -S "$docker_updater_runtime/updater.sock" ]]; then
      ui_ok 'Updater: socket Unix jest dostępny.'
    else
      ui_fail 'Updater: brakuje socketu Unix updater.sock.'
      failed=1
    fi
  else
    ui_fail 'Updater: cloudportal-updater.service nie jest aktywny.'
    failed=1
  fi

  if [[ -n "$release" && -f "$release/docker-compose.yml" && -r "$docker_env" ]]; then
    local compose_services_output=''
    if compose_services_output=$(docker_compose config --services 2>/dev/null); then
      mapfile -t compose_services <<< "$compose_services_output"
      for service in "${required_services[@]}"; do
        if printf '%s\n' "${compose_services[@]}" | grep -Fxq "$service"; then
          ui_ok "Definicja Compose: $service"
        else
          ui_fail "Brak wymaganej usługi w Compose: $service"
          failed=1
        fi
      done
    else
      ui_fail 'Nie udało się odczytać listy usług z Docker Compose.'
      failed=1
    fi
  else
    ui_fail 'Nie można zweryfikować definicji Compose bez aktywnego release i konfiguracji.'
    failed=1
  fi

  ui_header 'Cloudportal-backed — usługi Docker'

  for service in "${long_running_services[@]}"; do
    mapfile -t ids < <(
      docker ps -aq         --filter "label=com.docker.compose.project=$docker_project"         --filter "label=com.docker.compose.service=$service" 2>/dev/null || true
    )
    if (("${#ids[@]}" != 1)); then
      ui_fail "$service: oczekiwano 1 kontenera, znaleziono ${#ids[@]}."
      failed=1
      continue
    fi

    state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${ids[0]}" 2>/dev/null || true)
    if [[ "$state" != running ]]; then
      ui_fail "$service: status=${state:-unknown}, oczekiwano running."
      failed=1
      continue
    fi

    if [[ "$service" == postgres || "$service" == redis ]]; then
      if [[ "$health" == healthy ]]; then
        ui_ok "$service: running, health=healthy"
      else
        ui_fail "$service: running, health=${health:-unknown}; oczekiwano healthy."
        failed=1
      fi
    elif [[ "$service" == api && "$health" != none ]]; then
      if [[ "$health" == healthy ]]; then
        ui_ok "$service: running, health=healthy"
      else
        ui_fail "$service: running, health=${health:-unknown}; oczekiwano healthy."
        failed=1
      fi
    else
      ui_ok "$service: running"
    fi
  done

  mapfile -t ids < <(
    docker ps -aq       --filter "label=com.docker.compose.project=$docker_project"       --filter 'label=com.docker.compose.service=migrate' 2>/dev/null || true
  )
  if (("${#ids[@]}" != 1)); then
    ui_fail "migrate: oczekiwano 1 kontenera, znaleziono ${#ids[@]}."
    failed=1
  else
    state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
    exit_code=$(docker inspect --format '{{.State.ExitCode}}' "${ids[0]}" 2>/dev/null || true)
    if [[ "$state" == exited && "$exit_code" == 0 ]]; then
      ui_ok 'migrate: zakończony poprawnie, exit=0'
    else
      ui_fail "migrate: status=${state:-unknown}, exit=${exit_code:-unknown}; oczekiwano exited/0."
      failed=1
    fi
  fi

  if [[ "$expected_workers" =~ ^[0-9]+$ ]] && ((10#$expected_workers >= 1)); then
    mapfile -t ids < <(
      docker ps -aq         --filter "label=com.docker.compose.project=$docker_project"         --filter 'label=com.docker.compose.service=worker' 2>/dev/null || true
    )
    total_workers=${#ids[@]}
    running_workers=0
    for service in "${ids[@]}"; do
      state=$(docker inspect --format '{{.State.Status}}' "$service" 2>/dev/null || true)
      [[ "$state" == running ]] && ((running_workers+=1))
    done
    if ((running_workers == expected_workers && total_workers == expected_workers)); then
      ui_ok "worker: $running_workers/$expected_workers kontenerów running"
    else
      ui_fail "worker: running=$running_workers/$expected_workers, wszystkich kontenerów=$total_workers."
      failed=1
    fi
  else
    ui_fail "Nieprawidłowa wartość CP_WORKER_COUNT: ${expected_workers:-brak}"
    failed=1
  fi

  if [[ "$public_port" =~ ^[0-9]+$ ]] && command -v curl >/dev/null 2>&1; then
    if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:$public_port/api/v1/health" >/dev/null 2>&1; then
      ui_ok "HTTPS healthcheck: /api/v1/health odpowiada na porcie $public_port"
    else
      ui_fail "HTTPS healthcheck nie odpowiada na porcie $public_port."
      failed=1
    fi
  else
    ui_fail 'Nie można wykonać HTTPS healthchecku: brak curl albo nieprawidłowy CP_HTTPS_PORT.'
    failed=1
  fi

  if [[ "$public_port" =~ ^[0-9]+$ ]] && docker_updater_status_probe "$public_port"; then
    ui_ok 'Updater: /update-status odpowiada przez reverse proxy.'
  else
    ui_fail 'Updater: /update-status nie odpowiada poprawnie przez reverse proxy.'
    failed=1
  fi

  ui_info 'Bieżący stan kontenerów Compose:'
  if [[ -n "$release" && -f "$release/docker-compose.yml" && -r "$docker_env" ]]; then
    docker_compose ps -a || {
      ui_fail 'docker compose ps -a zakończył się błędem.'
      failed=1
    }
  else
    docker ps -a --filter "label=com.docker.compose.project=$docker_project" || true
  fi

  ui_header 'Podsumowanie statusu Docker'
  if ((failed == 0)); then
    ui_ok "Stack Docker kompletny i sprawny: postgres, redis, migrate, api, worker x$expected_workers, dispatcher, proxy."
    return 0
  fi

  ui_fail 'Stack Docker jest niekompletny albo co najmniej jedna usługa jest niesprawna.'
  ui_info "Diagnostyka: docker compose -p $docker_project logs --tail=100"
  return 1
}

docker_service_ready() {
  local service=$1 require_health=${2:-0}
  local ids=() state health

  mapfile -t ids < <(
    docker ps -aq \
      --filter "label=com.docker.compose.project=$docker_project" \
      --filter "label=com.docker.compose.service=$service" 2>/dev/null || true
  )
  (("${#ids[@]}" == 1)) || return 1

  state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
  [[ "$state" == running ]] || return 1

  if ((require_health)); then
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${ids[0]}" 2>/dev/null || true)
    if ((require_health == 1)); then
      [[ "$health" == healthy ]] || return 1
    elif [[ "$health" != none ]]; then
      [[ "$health" == healthy ]] || return 1
    fi
  fi
}

docker_wait_service_ready() {
  local service=$1 require_health=${2:-0} attempts=${3:-30}
  local attempt

  for ((attempt=1; attempt<=attempts; attempt++)); do
    if docker_service_ready "$service" "$require_health"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

docker_backend_network_probe() {
  docker_compose run --rm --no-deps api python -c '
import socket
for host, port in (("postgres", 5432), ("redis", 6379)):
    sock = socket.create_connection((host, port), 5)
    sock.close()
' >/dev/null 2>&1
}

docker_repair() {
  CURRENT_STAGE='auto-naprawa Docker'
  ui_header 'Cloudportal-backed — auto-naprawa Docker'

  local release expected_workers='1' public_port='8443'
  local docker_ready=0 https_ready=0 running_workers=0 total_workers=0
  local infra_repaired=0 network_repaired=0 migrate_repaired=0 api_repaired=0
  local ids=() state health exit_code service attempt

  if ! command -v docker >/dev/null 2>&1; then
    ui_fail 'Brak polecenia Docker; auto-naprawa nie może zostać wykonana.'
    return 1
  fi

  if ! docker info >/dev/null 2>&1; then
    ui_warn 'Docker Engine nie odpowiada; próbuję uruchomić usługę Docker.'
    if [[ $EUID -eq 0 ]] && command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
      systemctl start docker.service docker.socket >/dev/null 2>&1 || true
      for ((attempt=1; attempt<=10; attempt++)); do
        if docker info >/dev/null 2>&1; then
          docker_ready=1
          break
        fi
        sleep 1
      done
    fi
    if ((docker_ready == 0)); then
      ui_fail 'Nie udało się przywrócić Docker Engine.'
      ui_info 'Sprawdź: systemctl status docker oraz journalctl -u docker -n 100 --no-pager'
      return 1
    fi
    ui_ok 'Docker Engine został przywrócony.'
  fi

  docker_compose_detect || {
    ui_fail 'Docker Compose nie jest dostępny; auto-naprawa nie może zostać wykonana.'
    return 1
  }

  release=$(docker_current_release)
  [[ -n "$release" && -f "$release/docker-compose.yml" ]] || {
    ui_fail "Brak aktywnego release Docker z docker-compose.yml w $docker_root/current."
    return 1
  }
  [[ -r "$docker_env" ]] || {
    ui_fail "Brak konfiguracji Docker: $docker_env"
    return 1
  }

  expected_workers=$(sed -n 's/^CP_WORKER_COUNT=//p' "$docker_env" | tail -n 1)
  public_port=$(sed -n 's/^CP_HTTPS_PORT=//p' "$docker_env" | tail -n 1)
  expected_workers=${expected_workers:-1}
  public_port=${public_port:-8443}
  docker_valid_workers "$expected_workers" || {
    ui_fail "Nieprawidłowa wartość CP_WORKER_COUNT: ${expected_workers:-brak}"
    return 1
  }
  docker_valid_port "$public_port" || {
    ui_fail "Nieprawidłowa wartość CP_HTTPS_PORT: ${public_port:-brak}"
    return 1
  }

  ui_info 'Weryfikuję hostowy serwis updatera i jego tokeny.'
  docker_prepare_updater_config ''
  docker_activate_updater "$release"
  ui_ok 'Updater hostowy został uruchomiony i ma komplet tokenów.'

  ui_info 'Naprawiam zależności selektywnie; zdrowe kontenery nie będą odtwarzane.'

  for service in postgres redis; do
    if docker_service_ready "$service" 1; then
      ui_ok "$service: już działa i jest healthy."
      continue
    fi

    mapfile -t ids < <(
      docker ps -aq \
        --filter "label=com.docker.compose.project=$docker_project" \
        --filter "label=com.docker.compose.service=$service" 2>/dev/null || true
    )
    state=''
    health=''
    if (("${#ids[@]}" == 1)); then
      state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
      health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${ids[0]}" 2>/dev/null || true)
    fi

    ui_warn "$service: wymaga naprawy (status=${state:-missing}, health=${health:-unknown})."
    if (("${#ids[@]}" == 1)) && [[ "$state" == running ]]; then
      docker_compose restart "$service" || {
        ui_fail "Nie udało się zrestartować usługi $service."
        return 1
      }
    else
      docker_compose up -d "$service" || {
        ui_fail "Nie udało się uruchomić usługi $service."
        return 1
      }
    fi

    if ! docker_wait_service_ready "$service" 1 30; then
      ui_fail "$service nie osiągnął stanu running/healthy po naprawie."
      docker_compose logs --tail=80 "$service" || true
      return 1
    fi
    ui_ok "$service: przywrócony do running/healthy."
    infra_repaired=1
  done

  if docker_backend_network_probe; then
    ui_ok 'Sieć backend: nowy kontener osiąga PostgreSQL:5432 i Redis:6379.'
  else
    ui_warn 'Sieć backend: timeout/odmowa połączenia z PostgreSQL lub Redis mimo healthchecków.'
    ui_info 'Wymuszam odtworzenie wyłącznie kontenerów PostgreSQL i Redis; wolumeny pozostają bez zmian.'
    docker_compose up -d --no-deps --force-recreate postgres redis || {
      ui_fail 'Nie udało się odtworzyć kontenerów PostgreSQL/Redis.'
      return 1
    }

    if ! docker_wait_service_ready postgres 1 30 || ! docker_wait_service_ready redis 1 30; then
      ui_fail 'PostgreSQL lub Redis nie wrócił do stanu healthy po odtworzeniu.'
      docker_compose logs --tail=100 postgres redis || true
      return 1
    fi

    if ! docker_backend_network_probe; then
      ui_fail 'Ścieżka sieciowa backend → PostgreSQL/Redis nadal nie działa po odtworzeniu kontenerów.'
      ui_info "Diagnostyka sieci: docker network inspect ${docker_project}_backend"
      return 1
    fi
    ui_ok 'Sieć backend została przywrócona.'
    infra_repaired=1
    network_repaired=1
  fi

  mapfile -t ids < <(
    docker ps -aq \
      --filter "label=com.docker.compose.project=$docker_project" \
      --filter 'label=com.docker.compose.service=migrate' 2>/dev/null || true
  )
  state=''
  exit_code=''
  if (("${#ids[@]}" == 1)); then
    state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
    exit_code=$(docker inspect --format '{{.State.ExitCode}}' "${ids[0]}" 2>/dev/null || true)
  fi

  if [[ "$state" == exited && "$exit_code" == 0 ]]; then
    ui_ok 'migrate: poprzednia migracja zakończona poprawnie.'
  else
    ui_warn "migrate: odtwarzam migrację (status=${state:-missing}, exit=${exit_code:-unknown})."
    docker_compose up -d --no-deps migrate || {
      ui_fail 'Nie udało się uruchomić migracji.'
      return 1
    }
    migrate_repaired=1

    for ((attempt=1; attempt<=45; attempt++)); do
      mapfile -t ids < <(
        docker ps -aq \
          --filter "label=com.docker.compose.project=$docker_project" \
          --filter 'label=com.docker.compose.service=migrate' 2>/dev/null || true
      )
      if (("${#ids[@]}" == 1)); then
        state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
        exit_code=$(docker inspect --format '{{.State.ExitCode}}' "${ids[0]}" 2>/dev/null || true)
        if [[ "$state" == exited && "$exit_code" == 0 ]]; then
          break
        fi
        if [[ "$state" == exited && "$exit_code" != 0 ]]; then
          ui_fail "Migracja zakończyła się błędem, exit=$exit_code."
          docker_compose logs --tail=100 migrate || true
          return 1
        fi
      fi
      sleep 2
    done

    if [[ "$state" != exited || "$exit_code" != 0 ]]; then
      ui_fail 'Migracja nie zakończyła się poprawnie w oczekiwanym czasie.'
      docker_compose logs --tail=100 migrate || true
      return 1
    fi
    ui_ok 'migrate: naprawiona, exit=0.'
  fi

  if ((infra_repaired || migrate_repaired)) || ! docker_service_ready api 2; then
    mapfile -t ids < <(
      docker ps -aq \
        --filter "label=com.docker.compose.project=$docker_project" \
        --filter 'label=com.docker.compose.service=api' 2>/dev/null || true
    )
    state=''
    if (("${#ids[@]}" == 1)); then
      state=$(docker inspect --format '{{.State.Status}}' "${ids[0]}" 2>/dev/null || true)
    fi

    if ((network_repaired)); then
      ui_info 'API: odtwarzam kontener po naprawie ścieżki sieciowej.'
      docker_compose up -d --no-deps --force-recreate api || {
        ui_fail 'Nie udało się odtworzyć API.'
        return 1
      }
    elif (("${#ids[@]}" == 1)) && [[ "$state" == running ]]; then
      ui_info 'API: restart po naprawie zależności lub niesprawnym healthchecku.'
      docker_compose restart api || {
        ui_fail 'Nie udało się zrestartować API.'
        return 1
      }
    else
      ui_info 'API: uruchamiam brakujący lub zatrzymany kontener.'
      docker_compose up -d --no-deps api || {
        ui_fail 'Nie udało się uruchomić API.'
        return 1
      }
    fi
    api_repaired=1
  fi

  if ! docker_wait_service_ready api 2 45; then
    ui_fail 'API nie osiągnęło stanu running/healthy.'
    docker_compose logs --tail=100 api || true
    return 1
  fi
  ui_ok 'api: running i gotowe do obsługi ruchu.'

  mapfile -t ids < <(
    docker ps -aq \
      --filter "label=com.docker.compose.project=$docker_project" \
      --filter 'label=com.docker.compose.service=worker' 2>/dev/null || true
  )
  total_workers=${#ids[@]}
  running_workers=0
  for state in "${ids[@]}"; do
    [[ "$(docker inspect --format '{{.State.Status}}' "$state" 2>/dev/null || true)" == running ]] && ((running_workers+=1))
  done

  if ((network_repaired)); then
    ui_info "worker: odtwarzam kontenery po naprawie sieci i przywracam skalę $expected_workers."
    docker_compose up -d --no-deps --force-recreate --scale "worker=$expected_workers" worker || {
      ui_fail 'Nie udało się odtworzyć workerów.'
      return 1
    }
    running_workers=0
  else
    if ((total_workers > 0)); then
      ui_info 'worker: restart kontrolny, aby odświeżyć połączenia Redis/PostgreSQL.'
      docker_compose restart worker || true
      running_workers=0
    fi
    if ((running_workers != expected_workers || total_workers != expected_workers || infra_repaired || migrate_repaired)); then
      ui_info "worker: przywracam skalę $expected_workers."
      docker_compose up -d --no-deps --scale "worker=$expected_workers" worker || {
        ui_fail 'Nie udało się przywrócić workerów.'
        return 1
      }
    fi
  fi

  for ((attempt=1; attempt<=30; attempt++)); do
    mapfile -t ids < <(
      docker ps -aq \
        --filter "label=com.docker.compose.project=$docker_project" \
        --filter 'label=com.docker.compose.service=worker' 2>/dev/null || true
    )
    total_workers=${#ids[@]}
    running_workers=0
    for state in "${ids[@]}"; do
      [[ "$(docker inspect --format '{{.State.Status}}' "$state" 2>/dev/null || true)" == running ]] && ((running_workers+=1))
    done
    ((running_workers == expected_workers && total_workers == expected_workers)) && break
    sleep 2
  done
  if ((running_workers != expected_workers || total_workers != expected_workers)); then
    ui_fail "worker: running=$running_workers/$expected_workers, wszystkich kontenerów=$total_workers."
    docker_compose logs --tail=100 worker || true
    return 1
  fi
  ui_ok "worker: $running_workers/$expected_workers kontenerów running."

  if ((network_repaired)); then
    ui_info 'dispatcher: odtwarzam kontener po naprawie sieci.'
    docker_compose up -d --no-deps --force-recreate dispatcher || {
      ui_fail 'Nie udało się odtworzyć dispatchera.'
      return 1
    }
  elif docker_service_ready dispatcher 0; then
    ui_info 'dispatcher: restart kontrolny, aby odświeżyć połączenie z Redis.'
    docker_compose restart dispatcher || {
      ui_fail 'Nie udało się zrestartować dispatchera.'
      return 1
    }
  else
    ui_info 'dispatcher: uruchamiam brakujący lub zatrzymany kontener.'
    docker_compose up -d --no-deps dispatcher || {
      ui_fail 'Nie udało się uruchomić dispatchera.'
      return 1
    }
  fi
  if ! docker_wait_service_ready dispatcher 0 30; then
    ui_fail 'dispatcher nie osiągnął stanu running.'
    docker_compose logs --tail=100 dispatcher || true
    return 1
  fi
  ui_ok 'dispatcher: running.'

  if ((api_repaired)); then
    if docker_service_ready proxy 0; then
      ui_info 'proxy: restart po naprawie API, aby odświeżyć upstream.'
      docker_compose restart proxy || {
        ui_fail 'Nie udało się zrestartować proxy.'
        return 1
      }
    else
      docker_compose up -d --no-deps proxy || {
        ui_fail 'Nie udało się uruchomić proxy.'
        return 1
      }
    fi
  elif ! docker_service_ready proxy 0; then
    ui_info 'proxy: uruchamiam brakujący lub zatrzymany kontener.'
    docker_compose up -d --no-deps proxy || {
      ui_fail 'Nie udało się uruchomić proxy.'
      return 1
    }
  fi

  if ! docker_wait_service_ready proxy 0 20; then
    ui_fail 'proxy nie osiągnął stanu running.'
    docker_compose logs --tail=100 proxy || true
    return 1
  fi

  ui_info 'Oczekuję na końcowy HTTPS healthcheck.'
  for ((attempt=1; attempt<=30; attempt++)); do
    if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:$public_port/api/v1/health" >/dev/null 2>&1; then
      https_ready=1
      break
    fi
    sleep 2
  done

  if ((https_ready == 0)); then
    ui_warn 'HTTPS nadal nie odpowiada; wykonuję jeden celowany restart proxy.'
    docker_compose restart proxy || true
    for ((attempt=1; attempt<=15; attempt++)); do
      if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:$public_port/api/v1/health" >/dev/null 2>&1; then
        https_ready=1
        break
      fi
      sleep 2
    done
  fi

  if ((https_ready == 1)); then
    ui_ok "Stack odpowiedział na HTTPS healthcheck; workery running: $running_workers/$expected_workers."
    return 0
  fi

  ui_fail 'HTTPS healthcheck nadal nie odpowiada po selektywnej auto-naprawie.'
  docker_compose ps -a || true
  ui_info "Diagnostyka: docker compose -p $docker_project logs --tail=100 postgres redis api worker dispatcher proxy"
  return 1
}

docker_status() {
  if docker_status_check; then
    return 0
  fi

  if ((docker_auto_repair == 0)); then
    ui_warn 'Auto-naprawa Docker jest wyłączona przez --no-auto-repair.'
    return 1
  fi

  ui_warn 'Wykryto niesprawny stack Docker; uruchamiam jedną automatyczną próbę naprawy.'
  docker_repair || ui_warn 'Operacja naprawcza nie osiągnęła pełnej gotowości; wykonuję końcową walidację.'

  ui_header 'Cloudportal-backed — walidacja po auto-naprawie'
  if docker_status_check; then
    ui_ok 'Auto-naprawa Docker zakończyła się powodzeniem.'
    return 0
  fi

  ui_fail 'Auto-naprawa Docker nie przywróciła kompletnego, zdrowego stacka.'
  ui_info "Sprawdź logi: docker compose -p $docker_project logs --tail=100 postgres redis api worker dispatcher proxy"
  return 1
}


docker_uninstall() {
  ui_header 'Cloudportal-backed — deinstalacja Docker'

  if ((purge_data)); then
    ui_warn 'Tryb --purge-data usunie również nazwane wolumeny PostgreSQL, Redis, konfigurację i dane backendu.'
    if ((assume_yes == 0)); then
      if ((non_interactive)); then
        ui_fail '--non-interactive --uninstall --purge-data wymaga --yes.'
        exit 2
      fi
      [[ -r /dev/tty && -w /dev/tty ]] || { ui_fail 'Bez TTY użyj --yes razem z --uninstall --purge-data.'; exit 2; }
      printf 'Wpisz USUN, aby trwale usunąć dane Docker: ' >/dev/tty
      IFS= read -r confirmation </dev/tty || true
      [[ "$confirmation" == USUN ]] || { ui_warn 'Anulowano.'; exit 1; }
    fi
  else
    if ((assume_yes == 0)); then
      if ((non_interactive)); then
        ui_fail '--non-interactive --uninstall wymaga --yes.'
        exit 2
      fi
      [[ -r /dev/tty && -w /dev/tty ]] || { ui_fail 'Bez TTY użyj --yes razem z --uninstall.'; exit 2; }
      printf 'Zatrzymać i usunąć kontenery Cloudportal, zachowując wolumeny i konfigurację? [t/N] ' >/dev/tty
      IFS= read -r confirmation </dev/tty || true
      [[ "$confirmation" =~ ^[TtYy]$ ]] || { ui_warn 'Anulowano.'; exit 1; }
    fi
  fi

  ui_stage 1 3 'Zatrzymanie stacka'
  auto_update_remove
  systemctl disable --now cloudportal-updater.service >/dev/null 2>&1 || true
  rm -rf "$docker_updater_runtime"
  rm -f /etc/systemd/system/cloudportal-updater.service
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -f /usr/local/lib/cloudportal-updater/update-service.py
  rmdir /usr/local/lib/cloudportal-updater >/dev/null 2>&1 || true
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || {
    ui_fail 'Docker Engine nie jest dostępny; nie mogę bezpiecznie usunąć kontenerów ani wolumenów projektu.'
    exit 1
  }

  local release project_containers=() project_networks=() compose_available=0
  docker_compose_detect && compose_available=1 || ui_warn 'Docker Compose nie jest dostępny; użyję cleanupu po etykietach projektu.'
  release=$(docker_current_release)
  if ((compose_available)) && [[ -n "$release" && -f "$release/docker-compose.yml" && -r "$docker_env" ]]; then
    if ((purge_data)); then
      docker_compose down --remove-orphans --rmi local -v
    else
      docker_compose down --remove-orphans --rmi local
    fi
  else
    ui_warn 'Nie znaleziono kompletnej aktywnej konfiguracji Compose; wykonuję cleanup po etykiecie projektu.'
    mapfile -t project_containers < <(docker ps -aq --filter "label=com.docker.compose.project=$docker_project" 2>/dev/null || true)
    if ((${#project_containers[@]})); then
      docker rm -f "${project_containers[@]}" >/dev/null
      ui_info "Usunięto osierocone kontenery projektu: ${#project_containers[@]}"
    fi
    mapfile -t project_networks < <(docker network ls -q --filter "label=com.docker.compose.project=$docker_project" 2>/dev/null || true)
    if ((${#project_networks[@]})); then
      docker network rm "${project_networks[@]}" >/dev/null
    fi
  fi

  ui_stage 2 3 'Usunięcie runtime'
  rm -rf "$docker_root"
  ui_ok "Usunięto runtime: $docker_root"

  ui_stage 3 3 'Dane i konfiguracja'
  if ((purge_data)); then
    local project_volumes=()
    mapfile -t project_volumes < <(docker volume ls -q --filter "label=com.docker.compose.project=$docker_project" 2>/dev/null || true)
    if ((${#project_volumes[@]})); then
      docker volume rm "${project_volumes[@]}" >/dev/null
    fi
    rm -rf "$docker_config" "$docker_updater_data"
    ui_ok 'Usunięto konfigurację Docker, stan updatera i wolumeny aplikacji.'
  else
    ui_info "Zachowano konfigurację: $docker_config"
    ui_info 'Zachowano nazwane wolumeny Docker z bazą i danymi aplikacji.'
  fi
}

docker_prepare_github_curl() {
  local tmp_dir=$1
  DOCKER_CURL_ARGS=(-fsSL --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 180 --retry 3)
  if [[ -n "$github_config" ]]; then
    [[ -r "$github_config" && "$(stat -c %a "$github_config")" == 600 ]] || { ui_fail 'Plik --github-config musi istnieć, być czytelny i mieć tryb 600.'; exit 1; }
    DOCKER_CURL_ARGS+=(--config "$github_config")
  fi
  if [[ -n "$github_token_file" ]]; then
    [[ -r "$github_token_file" && "$(stat -c %a "$github_token_file")" == 600 ]] || { ui_fail 'Plik --github-token-file musi istnieć, być czytelny i mieć tryb 600.'; exit 1; }
    local github_token
    github_token=$(tr -d '\r\n' < "$github_token_file")
    [[ "$github_token" =~ ^[A-Za-z0-9._-]{20,512}$ ]] || { ui_fail 'Token GitHub ma nieprawidłowy format.'; exit 1; }
    printf 'header = "Authorization: Bearer %s"\n' "$github_token" > "$tmp_dir/curl.conf"
    chmod 0600 "$tmp_dir/curl.conf"
    unset github_token
    DOCKER_CURL_ARGS+=(--config "$tmp_dir/curl.conf")
  fi
}


docker_recovery_admin() {
  CURRENT_STAGE='recovery administratora Docker'
  ui_header 'Cloudportal-backed — recovery administratora Docker'

  docker_compose_detect || { ui_fail 'Docker Compose nie jest dostępny.'; return 1; }
  docker info >/dev/null 2>&1 || { ui_fail 'Docker Engine nie odpowiada. Uruchom recovery przez sudo na hoście z działającym Dockerem.'; return 1; }

  local release
  release=$(docker_current_release)
  [[ -n "$release" && -d "$release" && -f "$release/docker-compose.yml" ]] || {
    ui_fail "Brak aktywnego release Docker w $docker_root/current."
    return 1
  }
  [[ -r "$docker_env" ]] || { ui_fail "Brak konfiguracji Docker: $docker_env"; return 1; }

  ui_info 'Uruchamiam PostgreSQL wymagany do recovery.'
  docker_compose_for "$release" "$docker_env" up -d postgres
  local ready=0
  for ((attempt=1; attempt<=30; attempt++)); do
    if docker_compose_for "$release" "$docker_env" exec -T postgres pg_isready -U cloudportal -d cloudportal >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  ((ready == 1)) || {
    ui_fail 'PostgreSQL nie osiągnął stanu ready.'
    docker_compose_for "$release" "$docker_env" logs --tail=80 postgres || true
    return 1
  }

  recovery_prepare_inputs

  local args=(python -m app.recovery --username "$recovery_username" --password-stdin)
  [[ -z "$recovery_email" ]] || args+=(--email "$recovery_email")
  [[ -z "$recovery_project" ]] || args+=(--project "$recovery_project")

  ui_info 'Tworzę lub odzyskuję lokalne konto Administrator i przypisuję je do projektu.'
  if ! printf '%s\n' "$recovery_password" | docker_compose_for "$release" "$docker_env" run --rm --no-deps -T bootstrap "${args[@]}"; then
    unset recovery_password
    recovery_password=''
    ui_fail 'Recovery administratora nie powiodło się.'
    return 1
  fi
  unset recovery_password
  recovery_password=''

  ui_ok "Recovery zakończone. Konto: $recovery_username"
  ui_info "Projekt: ${recovery_project:-default}"
  ui_info 'Wszystkie wcześniejsze tokeny tego konta zostały unieważnione.'
}

docker_install() {
  ui_header 'Cloudportal-backed — instalacja Docker'
  local stages=6 release_sha effective_tarball_url candidate_sha release docker_tls_stage archive_sha release_ref
  local candidate_env previous_release tls_backup_dir=''
  local docker_tls_changed=0 had_previous_tls=0
  docker_tmp_dir=''
  previous_release=$(docker_current_release)
  local previous_docker_host='' previous_docker_port='' previous_docker_workers=''
  if [[ -r "$docker_env" ]]; then
    previous_docker_host=$(sed -n 's/^CP_PUBLIC_HOST=//p' "$docker_env" | tail -n 1)
    previous_docker_port=$(sed -n 's/^CP_HTTPS_PORT=//p' "$docker_env" | tail -n 1)
    previous_docker_workers=$(sed -n 's/^CP_WORKER_COUNT=//p' "$docker_env" | tail -n 1)
  fi
  backend_host=${backend_host:-${previous_docker_host:-$(hostname -f 2>/dev/null || hostname)}}
  backend_port=${backend_port:-${previous_docker_port:-8443}}
  workers=${workers:-${previous_docker_workers:-1}}

  docker_valid_host "$backend_host" || { ui_fail 'Nieprawidłowy host. Użyj nazwy DNS lub adresu bez schematu URL.'; exit 2; }
  docker_valid_port "$backend_port" || { ui_fail 'Nieprawidłowy port. Dozwolone 1-65535 z wyjątkiem 6389, 8765 i 8766.'; exit 2; }
  docker_valid_workers "$workers" || { ui_fail 'Nieprawidłowa liczba workerów. Dozwolone 1-64.'; exit 2; }
  backend_port=$((10#$backend_port))
  workers=$((10#$workers))
  [[ -z "$github_token_file" || -z "$github_config" ]] || { ui_fail 'Użyj tylko jednej opcji: --github-token-file albo --github-config.'; exit 2; }
  [[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != *..* ]] || { ui_fail 'Nieprawidłowy Git ref.'; exit 2; }
  [[ -z "$cert_file" && -z "$cert_key" || -r "$cert_file" && -r "$cert_key" ]] || { ui_fail 'Podaj oba pliki TLS: --cert-file i --cert-key.'; exit 2; }
  [[ -z "$backup_schedule" ]] || { ui_fail '--enable-backups/--disable-backups dotyczą instalacji natywnej; tryb --docker nie zarządza jeszcze harmonogramem backupu.'; exit 2; }

  ui_stage 1 "$stages" 'Pretest Docker'
  docker_preflight || exit 1
  ui_ok 'Pretest zakończony bez zmian w systemie.'
  docker_install_dependencies
  docker info >/dev/null
  docker_compose_detect
  ui_ok "Docker Engine i Compose są dostępne: ${DOCKER_COMPOSE[*]}"

  ui_stage 2 "$stages" 'Pobieranie aplikacji'
  install -d -m 0755 "$docker_root" "$docker_root/releases"
  install -d -m 0700 "$docker_config" "$docker_tls"
  docker_tmp_dir=$(mktemp -d)
  trap '[[ -z "${docker_tmp_dir:-}" ]] || rm -rf "$docker_tmp_dir"' EXIT
  docker_prepare_github_curl "$docker_tmp_dir"
  ui_info "Pobieram kod źródłowy z GitHub: $repo @ $ref"
  release_ref=${update_channel_ref:-$ref}
  release_sha=${CLOUDPORTAL_RELEASE_SHA:-}
  [[ -z "$release_sha" || "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]] || {
    ui_fail 'CLOUDPORTAL_RELEASE_SHA ma nieprawidłowy format.'
    exit 1
  }
  effective_tarball_url=$(curl "${DOCKER_CURL_ARGS[@]}" -w '%{url_effective}' "https://api.github.com/repos/$repo/tarball/$ref" -o "$docker_tmp_dir/source.tar.gz") || {
    ui_fail 'Nie udało się pobrać kodu źródłowego z GitHub.'
    exit 1
  }
  candidate_sha=${effective_tarball_url##*/}
  archive_sha=$(sha256sum "$docker_tmp_dir/source.tar.gz" | awk '{print $1}')
  if [[ -z "$release_sha" ]]; then
    [[ "$candidate_sha" =~ ^[0-9a-fA-F]{40}$ ]] && release_sha=${candidate_sha,,} || release_sha=$archive_sha
  else
    release_sha=${release_sha,,}
  fi
  mkdir "$docker_tmp_dir/source"
  tar -xzf "$docker_tmp_dir/source.tar.gz" -C "$docker_tmp_dir/source" --strip-components=1 --no-same-owner
  [[ -f "$docker_tmp_dir/source/Dockerfile" && -f "$docker_tmp_dir/source/docker-compose.yml" && -f "$docker_tmp_dir/source/scripts/nginx-container.conf" ]] || {
    ui_fail 'Pobrane archiwum nie zawiera kompletnej konfiguracji Docker Cloudportal.'
    exit 1
  }
  release=$(mktemp -d "$docker_root/releases/$(date -u +%Y%m%dT%H%M%SZ)-${release_sha:0:12}-XXXXXX")
  cp -a "$docker_tmp_dir/source/." "$release/"
  chmod -R go-w "$release"
  "$python_command" - "$release/.cloudportal-release.json" "$repo" "$release_ref" "$release_sha" "$archive_sha" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    'repository': sys.argv[2],
    'ref': sys.argv[3],
    'commit_sha': sys.argv[4],
    'archive_sha256': sys.argv[5],
    'installed_at': datetime.now(timezone.utc).isoformat(),
}, indent=2) + '\n')
PY
  chmod 0644 "$release/.cloudportal-release.json"
  ui_ok "Przygotowano kandydata release: $release"

  ui_stage 3 "$stages" 'Konfiguracja i sekrety'
  local postgres_password=''
  if [[ -r "$docker_env" ]]; then
    postgres_password=$(sed -n 's/^CP_POSTGRES_PASSWORD=//p' "$docker_env" | tail -n 1)
  elif [[ -r "$docker_pending_env" ]]; then
    postgres_password=$(sed -n 's/^CP_POSTGRES_PASSWORD=//p' "$docker_pending_env" | tail -n 1)
  fi
  if [[ -z "$postgres_password" ]]; then
    postgres_password=$(openssl rand -hex 32)
  fi
  umask 077
  candidate_env="$docker_pending_env"
  cat > "$candidate_env" <<EOF
CP_POSTGRES_PASSWORD=$postgres_password
CP_BUILD_COMMIT=$release_sha
CP_HTTPS_PORT=$backend_port
CP_TLS_DIR=$docker_tls
CP_PUBLIC_HOST=$backend_host
CP_WORKER_COUNT=$workers
CP_UPDATER_HOST_CONFIG_DIR=$docker_config
CP_UPDATER_RUNTIME_DIR=$docker_updater_runtime
EOF
  chmod 0600 "$candidate_env"
  unset postgres_password

  docker_prepare_updater_config "$release_ref"
  ui_ok 'Tokeny i konfiguracja hostowego updatera są gotowe dla kontenerów.'

  docker_tls_stage="$docker_tmp_dir/tls-stage"
  if [[ -n "$cert_file" ]]; then
    install -d -m 0700 "$docker_tls_stage"
    install -m 0600 "$cert_file" "$docker_tls_stage/server.crt"
    install -m 0600 "$cert_key" "$docker_tls_stage/server.key"
    docker_certificate_key_matches "$docker_tls_stage/server.crt" "$docker_tls_stage/server.key" || {
      ui_fail 'Własny certyfikat TLS i klucz nie pasują do siebie.'
      exit 1
    }
    openssl x509 -in "$docker_tls_stage/server.crt" -noout -checkend 300 >/dev/null 2>&1 || {
      ui_fail 'Własny certyfikat TLS jest nieważny albo wygasa w ciągu 5 minut.'
      exit 1
    }
    docker_certificate_matches_host "$docker_tls_stage/server.crt" || {
      ui_fail "Własny certyfikat TLS nie obejmuje hosta $backend_host."
      exit 1
    }
    printf '%s\n' custom > "$docker_tls_stage/source"
    printf '%s\n' "$backend_host" > "$docker_tls_stage/host"
    docker_tls_changed=1
  else
    local regenerate_tls=0 previous_tls_host='' previous_tls_source=''
    [[ -r "$docker_tls/host" ]] && previous_tls_host=$(tr -d '\r\n' < "$docker_tls/host")
    [[ -r "$docker_tls/source" ]] && previous_tls_source=$(tr -d '\r\n' < "$docker_tls/source")

    if [[ "$previous_tls_source" == custom ]]; then
      [[ -r "$docker_tls/server.crt" && -r "$docker_tls/server.key" ]] || {
        ui_fail 'Instalacja używa własnego TLS, ale brakuje zapisanego certyfikatu lub klucza. Podaj ponownie --cert-file i --cert-key.'
        exit 1
      }
      [[ "$previous_tls_host" == "$backend_host" ]] || {
        ui_fail "Istniejący własny certyfikat TLS był skonfigurowany dla hosta ${previous_tls_host:-?}. Przy zmianie hosta podaj nowy --cert-file i --cert-key."
        exit 1
      }
      docker_certificate_key_matches "$docker_tls/server.crt" "$docker_tls/server.key" || {
        ui_fail 'Zapisany własny certyfikat TLS i klucz nie pasują do siebie. Podaj poprawny --cert-file i --cert-key.'
        exit 1
      }
      openssl x509 -in "$docker_tls/server.crt" -noout -checkend 300 >/dev/null 2>&1 || {
        ui_fail 'Zapisany własny certyfikat TLS jest nieważny albo wygasa w ciągu 5 minut. Podaj nowy --cert-file i --cert-key.'
        exit 1
      }
      docker_certificate_matches_host "$docker_tls/server.crt" || {
        ui_fail "Zapisany własny certyfikat TLS nie obejmuje hosta $backend_host. Podaj nowy --cert-file i --cert-key."
        exit 1
      }
      ui_info 'Zachowuję istniejący własny certyfikat TLS.'
    else
      [[ -r "$docker_tls/server.crt" && -r "$docker_tls/server.key" ]] || regenerate_tls=1
      [[ "$previous_tls_source" == managed-self-signed ]] || regenerate_tls=1
      [[ "$previous_tls_host" == "$backend_host" ]] || regenerate_tls=1
      if ((regenerate_tls == 0)); then
        openssl x509 -in "$docker_tls/server.crt" -noout -checkend 86400 >/dev/null 2>&1 || regenerate_tls=1
        docker_certificate_matches_host "$docker_tls/server.crt" || regenerate_tls=1
        docker_certificate_key_matches "$docker_tls/server.crt" "$docker_tls/server.key" || regenerate_tls=1
      fi
      if ((regenerate_tls)); then
        docker_generate_managed_tls "$docker_tls_stage"
        docker_tls_changed=1
        ui_info "Przygotowano nowy self-signed TLS dla $backend_host; zostanie aktywowany dopiero po udanym buildzie i bootstrapie."
      fi
    fi
  fi
  ui_ok "Konfiguracja kandydata Docker: $candidate_env"

  ui_stage 4 "$stages" 'Budowa obrazu'
  docker_compose_for "$release" "$candidate_env" build
  ui_ok 'Obraz Cloudportal został zbudowany.'

  ui_stage 5 "$stages" 'Klucz szyfrujący'
  ui_info 'Uruchamiam wyłącznie PostgreSQL, bez migracji, aby bezpiecznie zweryfikować istniejący master key.'
  docker_compose_for "$release" "$candidate_env" up -d postgres
  local postgres_ready=0
  for ((attempt=1; attempt<=30; attempt++)); do
    if docker_compose_for "$release" "$candidate_env" exec -T postgres pg_isready -U cloudportal -d cloudportal >/dev/null 2>&1; then
      postgres_ready=1
      break
    fi
    sleep 1
  done
  ((postgres_ready == 1)) || {
    ui_fail 'PostgreSQL kandydata Docker nie osiągnął stanu ready przed weryfikacją master key.'
    docker_compose_for "$release" "$candidate_env" logs --tail=80 postgres || true
    exit 1
  }
  ui_info 'Tworzę lub weryfikuję master key bez generowania jednorazowych danych administratora i bez uruchamiania migracji.'
  docker_compose_for "$release" "$candidate_env" run --rm --no-deps -T bootstrap python -m app.bootstrap --key-only
  ui_ok 'Master key jest gotowy; migracje wykona usługa migrate podczas startu kandydata.'

  if ((docker_tls_changed)); then
    tls_backup_dir="$docker_tmp_dir/tls-backup"
    if [[ -f "$docker_tls/server.crt" || -f "$docker_tls/server.key" || -f "$docker_tls/source" || -f "$docker_tls/host" ]]; then
      install -d -m 0700 "$tls_backup_dir"
      cp -a "$docker_tls/." "$tls_backup_dir/"
      had_previous_tls=1
    fi
    install -d -m 0700 "$docker_tls"
    install -m 0600 "$docker_tls_stage/server.crt" "$docker_tls/server.crt"
    install -m 0600 "$docker_tls_stage/server.key" "$docker_tls/server.key"
    install -m 0600 "$docker_tls_stage/source" "$docker_tls/source"
    install -m 0600 "$docker_tls_stage/host" "$docker_tls/host"
    ui_ok 'Nowy materiał TLS został przygotowany do walidacji kandydata.'
  fi

  ui_stage 6 "$stages" 'Start stacka i healthcheck'
  if ! docker_compose_for "$release" "$candidate_env" up -d --remove-orphans --scale "worker=$workers"; then
    docker_rollback_candidate "$release" "$candidate_env" "$previous_release" "$previous_docker_workers" "$docker_tls_changed" "$tls_backup_dir" "$had_previous_tls"
    ui_fail 'Nie udało się uruchomić kandydata Docker; poprzedni aktywny release pozostaje źródłem prawdy.'
    exit 1
  fi
  if ((docker_tls_changed)) && ! docker_compose_for "$release" "$candidate_env" restart proxy; then
    docker_rollback_candidate "$release" "$candidate_env" "$previous_release" "$previous_docker_workers" "$docker_tls_changed" "$tls_backup_dir" "$had_previous_tls"
    ui_fail 'Nie udało się przeładować proxy z nowym TLS; przywrócono poprzedni stan.'
    exit 1
  fi
  local ready=0
  local docker_tls_source=''
  local docker_health_curl=(-fsS --connect-timeout 2 --max-time 5)
  [[ -r "$docker_tls/source" ]] && docker_tls_source=$(tr -d '\r\n' < "$docker_tls/source")
  if [[ "$docker_tls_source" == managed-self-signed ]] || docker_certificate_is_self_signed "$docker_tls/server.crt"; then
    docker_health_curl+=(--cacert "$docker_tls/server.crt")
  fi
  for ((attempt=1; attempt<=45; attempt++)); do
    if curl "${docker_health_curl[@]}" --resolve "$backend_host:$backend_port:127.0.0.1" "https://$backend_host:$backend_port/api/v1/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  ((ready == 1)) || {
    ui_fail 'Kandydat Docker wystartował, ale HTTPS healthcheck nie przeszedł.'
    docker_compose_for "$release" "$candidate_env" ps || true
    docker_compose_for "$release" "$candidate_env" logs --tail=100 api proxy || true
    docker_rollback_candidate "$release" "$candidate_env" "$previous_release" "$previous_docker_workers" "$docker_tls_changed" "$tls_backup_dir" "$had_previous_tls"
    exit 1
  }
  ui_ok 'Healthcheck HTTPS kandydata zakończony pomyślnie.'

  mv -f "$candidate_env" "$docker_env"
  ln -sfn "$release" "$docker_root/current"
  ui_ok 'Kandydat został aktywowany jako bieżący release Docker.'

  docker_activate_updater "$release"
  ui_ok 'Niezależny hostowy updater jest aktywny.'

  ui_info 'Finalizuję bootstrap administratora dopiero po udanym healthchecku. Jednorazowy token, jeżeli powstanie, zostanie wyświetlony poniżej.'
  docker_compose_for "$release" "$docker_env" run --rm --no-deps -T bootstrap python -m app.bootstrap --url "https://$backend_host:$backend_port"
  ui_ok 'Bootstrap administratora zakończony po walidacji działającego stacka.'

  local updater_ready=0
  for ((attempt=1; attempt<=20; attempt++)); do
    if docker_updater_status_probe "$backend_port"; then
      updater_ready=1
      break
    fi
    sleep 1
  done
  ((updater_ready == 1)) || {
    ui_fail 'Updater nie odpowiada przez /update-status po uruchomieniu stacka.'
    systemctl --no-pager --full status cloudportal-updater.service || true
    journalctl --no-pager -u cloudportal-updater.service -n 80 || true
    exit 1
  }
  ui_ok 'Kanał /update-status i token statusu działają poprawnie.'

  if ((update_in_progress)); then
    local updater_reload_unit="cloudportal-docker-updater-reload-$"
    if systemd-run --quiet --collect --unit="$updater_reload_unit" --on-active=8s \
        /bin/systemctl restart cloudportal-updater.service >/dev/null 2>&1; then
      ui_info 'Nowa wersja updatera zostanie przeładowana po zakończeniu tej aktualizacji.'
    else
      ui_warn 'Nie udało się zaplanować przeładowania updatera; bieżąca aktualizacja jest zakończona.'
    fi
  fi

  ui_header 'Podsumowanie'
  ui_ok 'Instalacja Docker Cloudportal-backed zakończona.'
  ui_info "Panel: https://$backend_host:$backend_port/ui/"
  ui_info "Runtime: $release"
  ui_info "Konfiguracja: $docker_config"
  ui_info "Workery: $workers"
  ui_info 'Status: sudo ./install.sh --docker --status'
  ui_info 'Logi: docker compose -p cloudportal-backed logs'
  rm -rf "$docker_tmp_dir"
  docker_tmp_dir=''
  trap - EXIT
}

if ((auto_update_mode)); then
  if [[ "$auto_update_action" != status ]]; then
    command -v flock >/dev/null 2>&1 || { ui_fail 'Zarządzanie cron wymaga flock.'; exit 1; }
    exec {AUTO_UPDATE_CONFIG_LOCK_FD}>/run/cloudportal-install.lock
    flock --exclusive --nonblock "$AUTO_UPDATE_CONFIG_LOCK_FD" || {
      ui_fail 'Instalator jest zajęty; harmonogram nie został zmieniony.'
      exit 1
    }
  fi
  auto_update_manage
  drain_script_input
  exit 0
fi

if ((docker_mode)); then
  if ((recovery_mode)); then
    docker_acquire_install_lock
    docker_recovery_admin
    exit 0
  fi
  if ((status_mode)); then
    ((docker_auto_repair == 0)) || docker_acquire_install_lock
    if docker_status; then
      exit 0
    fi
    exit 1
  fi
  docker_acquire_install_lock
  if ((uninstall_mode)); then
    docker_uninstall
    exit 0
  fi
  docker_install
  exit 0
fi

command -v systemctl >/dev/null || { ui_fail 'Brak systemctl. Instalator wymaga systemd.'; exit 1; }
[[ -d /run/systemd/system ]] || { ui_fail 'systemd nie jest uruchomiony. Dla kontenera użyj Docker Compose.'; exit 1; }
config=/etc/cloudportal-backed
app_root=/opt/cloudportal-backed
data=/var/lib/cloudportal-backed
if [[ -r "$config/public.conf" ]]; then
  previous_host=$(sed -n 's/^host=//p' "$config/public.conf")
  previous_port=$(sed -n 's/^port=//p' "$config/public.conf")
fi
backend_host=${backend_host:-${previous_host:-$(hostname -f)}}
backend_port=${backend_port:-${previous_port:-8443}}
if [[ -r "$config/backend.env" ]]; then
  previous_workers=$(sed -n 's/^CP_WORKER_COUNT=//p' "$config/backend.env")
  previous_backup_schedule=$(sed -n 's/^CP_BACKUP_SCHEDULE_ENABLED=//p' "$config/backend.env")
  previous_backup_retention_days=$(sed -n 's/^CP_BACKUP_RETENTION_DAYS=//p' "$config/backend.env")
fi
workers=${workers:-${previous_workers:-1}}
backup_schedule=${backup_schedule:-${previous_backup_schedule:-false}}
backup_retention_days=${backup_retention_days:-${previous_backup_retention_days:-14}}
valid_host() {
  [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$ ]]
}
valid_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535 && 10#$1 != 6389 && 10#$1 != 8765 && 10#$1 != 8766))
}
valid_workers() {
  [[ "$1" =~ ^[0-9]{1,2}$ ]] && ((10#$1 >= 1 && 10#$1 <= 64))
}
valid_retention() {
  [[ "$1" =~ ^[0-9]{1,4}$ ]] && ((10#$1 >= 1 && 10#$1 <= 3650))
}
((gui == 0 || non_interactive == 0)) || { echo '--gui/-gui cannot be combined with --non-interactive.' >&2; exit 2; }

lock_file=/run/cloudportal-install.lock
lock_owner_file=/run/cloudportal-install.owner
lock_holder_pid=''

stop_cloudportal_application() {
  ui_info 'Zatrzymuję usługi aplikacyjne Cloudportal...'
  if ((update_in_progress)); then
    ui_info 'Serwis updatera pozostaje aktywny na czas aktualizacji.'
  else
    systemctl stop cloudportal-updater.service >/dev/null 2>&1 || true
  fi
  systemctl stop cloudportal-backup.timer cloudportal-backup.service >/dev/null 2>&1 || true
  systemctl stop cloudportal-dispatcher.service cloudportal-api.service >/dev/null 2>&1 || true

  local worker_units=()
  mapfile -t worker_units < <(
    systemctl list-units --all --type=service --no-legend --no-pager 'cloudportal-worker@*.service' 2>/dev/null       | awk '{print $1}'       | grep -E '^cloudportal-worker@.+\.service$' || true
  )
  if ((${#worker_units[@]})); then
    systemctl stop "${worker_units[@]}" >/dev/null 2>&1 || true
  fi
}

proc_lock_pids() {
  local fd target pid
  shopt -s nullglob
  for fd in /proc/[0-9]*/fd/*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    case "$target" in
      "$lock_file"|"$lock_file (deleted)")
        pid=${fd#/proc/}
        pid=${pid%%/*}
        [[ "$pid" =~ ^[0-9]+$ ]] && printf '%s\n' "$pid"
        ;;
    esac
  done
  shopt -u nullglob
}

lock_owner_pids() {
  local pid path recorded='' owner_source
  for owner_source in "$lock_owner_file" "$lock_file"; do
    if [[ -r "$owner_source" ]]; then
      recorded=$(head -n 1 "$owner_source" 2>/dev/null || true)
      if [[ "$recorded" =~ ^[0-9]+$ ]] && kill -0 "$recorded" 2>/dev/null; then
        printf '%s\n' "$recorded"
      fi
    fi
  done

  if command -v lslocks >/dev/null 2>&1; then
    while read -r pid path; do
      [[ "$path" == "$lock_file" && "$pid" =~ ^[0-9]+$ ]] || continue
      printf '%s\n' "$pid"
    done < <(lslocks -n -o PID,PATH 2>/dev/null || true)
  fi
  proc_lock_pids
}

collect_descendants() {
  local parent=$1 child
  while read -r child; do
    [[ "$child" =~ ^[0-9]+$ ]] || continue
    collect_descendants "$child"
    printf '%s\n' "$child"
  done < <(ps -eo pid=,ppid= 2>/dev/null | awk -v parent="$parent" '$2 == parent {print $1}')
}

terminate_process_tree() {
  local owner=$1 signal=${2:-TERM}
  [[ "$owner" =~ ^[0-9]+$ ]] || return 0
  ((owner > 1)) || return 0
  ((owner != $$)) || return 0
  kill -0 "$owner" 2>/dev/null || return 0

  local descendants=()
  mapfile -t descendants < <(collect_descendants "$owner")
  if ((${#descendants[@]})); then
    kill "-$signal" "${descendants[@]}" 2>/dev/null || true
  fi
  kill "-$signal" "$owner" 2>/dev/null || true
}

stop_previous_installer() {
  local signal=${1:-TERM} owner
  local owners=()
  mapfile -t owners < <(lock_owner_pids | awk '!seen[$0]++')
  for owner in "${owners[@]}"; do
    local command_line=''
    if [[ -r "/proc/$owner/cmdline" ]]; then
      command_line=$(tr '\0' ' ' < "/proc/$owner/cmdline" 2>/dev/null || true)
    fi
    echo "Stopping lock holder PID $owner${command_line:+ ($command_line)}..."
    terminate_process_tree "$owner" "$signal"
  done
}

try_acquire_install_lock() {
  local ready_file holder attempt
  ready_file=$(mktemp /run/cloudportal-install.ready.XXXXXX)
  rm -f "$ready_file"

  flock --exclusive --nonblock --close "$lock_file" sh -c '
    installer_pid=$1
    ready_file=$2
    owner_file=$3
    printf "%s\n" "$installer_pid" > "$owner_file"
    chmod 0600 "$owner_file" 2>/dev/null || true
    : > "$ready_file"
    trap "rm -f \"$owner_file\" \"$ready_file\"" EXIT
    while kill -0 "$installer_pid" 2>/dev/null; do sleep 1; done
  ' cloudportal-lock "$$" "$ready_file" "$lock_owner_file" &
  holder=$!

  for ((attempt=1; attempt<=40; attempt++)); do
    if [[ -e "$ready_file" ]]; then
      lock_holder_pid=$holder
      printf '%s\n' "$$" > "$lock_file"
      chmod 0600 "$lock_file" 2>/dev/null || true
      rm -f "$ready_file"
      return 0
    fi
    if ! kill -0 "$holder" 2>/dev/null; then
      wait "$holder" 2>/dev/null || true
      rm -f "$ready_file"
      return 1
    fi
    sleep 0.05
  done

  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  rm -f "$ready_file"
  return 1
}

acquire_install_lock() {
  if try_acquire_install_lock; then
    return 0
  fi

  if ((takeover_running_install == 0)); then
    ui_fail 'Inna instalacja jest aktywna, a --no-takeover zabrania jej zatrzymania. Usuń --no-takeover albo zakończ poprzedni proces.'
    exit 1
  fi

  ui_warn 'Wykryto inną instalację. Zatrzymuję Cloudportal i przejmuję blokadę instalatora...'
  stop_cloudportal_application
  stop_previous_installer TERM

  local attempt
  for ((attempt=1; attempt<=15; attempt++)); do
    if try_acquire_install_lock; then
      ui_ok 'Poprzednia instalacja zatrzymana; blokada przejęta.'
      return 0
    fi
    sleep 1
  done

  ui_warn 'Poprzedni instalator nadal trzyma blokadę; wymuszam zatrzymanie pozostałych procesów locka...'
  stop_previous_installer KILL
  for ((attempt=1; attempt<=10; attempt++)); do
    if try_acquire_install_lock; then
      ui_ok 'Poprzednia instalacja wymuszona; blokada przejęta.'
      return 0
    fi
    sleep 1
  done

  ui_fail 'Nie udało się przejąć /run/cloudportal-install.lock nawet po skanowaniu /proc/*/fd.'
  ui_info 'Sprawdź: sudo ./install.sh --status. Awaryjnie użyj: sudo ./install.sh --uninstall'
  exit 1
}

service_status_line() {
  local unit=$1 label=$2
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    ui_ok "$label: aktywny"
  elif systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q .; then
    ui_warn "$label: nieaktywny"
  else
    ui_info "$label: nie zainstalowano"
  fi
}

show_status() {
  CURRENT_STAGE='status'
  ui_header 'Cloudportal-backed — status'
  auto_update_show_status

  if [[ -L "$app_root/current" || -d "$app_root/current" ]]; then
    local current_release
    current_release=$(readlink -f "$app_root/current" 2>/dev/null || printf '%s' "$app_root/current")
    ui_ok "Runtime: $current_release"
  elif [[ -d "$app_root" ]]; then
    ui_warn "Runtime istnieje w $app_root, ale brak aktywnego symlink current."
  else
    ui_info "Runtime: nie zainstalowano w $app_root"
  fi

  if [[ -r "$config/public.conf" ]]; then
    local public_host public_port
    public_host=$(sed -n 's/^host=//p' "$config/public.conf")
    public_port=$(sed -n 's/^port=//p' "$config/public.conf")
    ui_info "Endpoint: https://${public_host:-?}:${public_port:-?}/"
  else
    ui_info "Endpoint: brak czytelnego $config/public.conf"
  fi

  service_status_line cloudportal-api.service 'API'
  service_status_line cloudportal-dispatcher.service 'Dispatcher'
  service_status_line cloudportal-redis.service 'Redis/Valkey'
  service_status_line nginx.service 'Nginx'
  service_status_line cloudportal-backup.timer 'Backup timer'

  local worker_units=()
  mapfile -t worker_units < <(
    systemctl list-units --all --type=service --no-legend --no-pager 'cloudportal-worker@*.service' 2>/dev/null |
      awk '{print $1}' |
      grep -E '^cloudportal-worker@.+\.service$' || true
  )
  if ((${#worker_units[@]})); then
    local active_workers=0 unit
    for unit in "${worker_units[@]}"; do
      systemctl is-active --quiet "$unit" 2>/dev/null && ((active_workers+=1)) || true
    done
    ui_info "Workery: $active_workers/${#worker_units[@]} aktywne"
  else
    ui_info 'Workery: brak jednostek'
  fi

  if command -v curl >/dev/null 2>&1 && curl -fsS --connect-timeout 2 --max-time 4       http://127.0.0.1:8765/api/v1/health >/dev/null 2>&1; then
    ui_ok 'Healthcheck lokalnego API: OK'
  else
    ui_warn 'Healthcheck lokalnego API: brak odpowiedzi na 127.0.0.1:8765'
  fi

  local owners=()
  mapfile -t owners < <(lock_owner_pids | awk '!seen[$0]++')
  if ((${#owners[@]})); then
    ui_warn "Lock instalatora jest aktywny; PID: ${owners[*]}"
  else
    ui_ok 'Lock instalatora: wolny'
  fi
}

preflight_checks() {
  local failed=0
  ui_info "System: $NAME $VERSION_ID · $arch · rodzina $os_family"
  ui_info "Cel: https://$backend_host:$backend_port · workery: $workers · ref: $ref"

  local command
  for command in systemctl flock awk sed grep tar sha256sum ps readlink df hostname; do
    if ! command -v "$command" >/dev/null 2>&1; then
      ui_fail "Brak wymaganej komendy: $command"
      failed=1
    fi
  done

  case "$os_family" in
    debian) command -v apt-get >/dev/null 2>&1 || { ui_fail 'Brak apt-get.'; failed=1; };;
    rhel) command -v dnf >/dev/null 2>&1 || { ui_fail 'Brak dnf.'; failed=1; };;
  esac

  local free_kib
  free_kib=$(df -Pk / 2>/dev/null | awk 'NR==2 {print $4}')
  if [[ "$free_kib" =~ ^[0-9]+$ ]]; then
    if ((free_kib < 2097152)); then
      ui_fail "Za mało wolnego miejsca: $((free_kib / 1024)) MiB. Wymagane minimum 2 GiB, zalecane 10 GiB."
      failed=1
    elif ((free_kib < 10485760)); then
      ui_warn "Wolne miejsce: $((free_kib / 1024)) MiB. Zalecane co najmniej 10 GiB."
    else
      ui_ok "Wolne miejsce: $((free_kib / 1024 / 1024)) GiB"
    fi
  else
    ui_warn 'Nie udało się ustalić wolnego miejsca na dysku.'
  fi

  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --connect-timeout 5 --max-time 10 https://api.github.com/ >/dev/null 2>&1; then
      ui_ok 'Połączenie HTTPS z api.github.com'
    else
      ui_fail 'Brak połączenia z api.github.com. Sprawdź DNS, routing, proxy/firewall i czas systemowy.'
      failed=1
    fi
  else
    ui_warn 'curl nie jest jeszcze dostępny; zostanie zainstalowany przez menedżer pakietów.'
  fi

  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$backend_port$"; then
    if [[ ${previous_port:-} == "$backend_port" ]]; then
      ui_info "Port $backend_port jest już używany przez istniejącą konfigurację; reinstalacja może go ponownie wykorzystać."
    else
      ui_warn "Port $backend_port jest już zajęty. Jeżeli nie należy do Cloudportal, Nginx może nie wystartować."
    fi
  else
    ui_ok "Port $backend_port nie wykazuje konfliktu nasłuchu."
  fi

  ((failed == 0)) || {
    ui_fail 'Pretest nie przeszedł. Popraw wskazane problemy przed instalacją.'
    return 1
  }
}

uninstall_preflight() {
  local failed=0 command
  for command in systemctl flock awk grep ps readlink pkill rm id; do
    if ! command -v "$command" >/dev/null 2>&1; then
      ui_fail "Deinstalacja wymaga komendy: $command"
      failed=1
    fi
  done

  if ((purge_data)); then
    for command in runuser psql dropdb dropuser; do
      if ! command -v "$command" >/dev/null 2>&1; then
        ui_fail "Pełny purge wymaga komendy PostgreSQL/systemowej: $command"
        failed=1
      fi
    done
    id postgres >/dev/null 2>&1 || {
      ui_fail 'Pełny purge wymaga lokalnego użytkownika systemowego postgres.'
      failed=1
    }
  fi

  ((failed == 0)) || {
    ui_fail 'Pretest deinstalacji nie przeszedł. Nic nie zostało usunięte.'
    return 1
  }
  ui_ok 'Pretest deinstalacji zakończony.'
}

confirm_uninstall() {
  ((assume_yes)) && return 0

  if ((non_interactive)); then
    ui_fail 'Tryb --non-interactive z --uninstall wymaga jawnego --yes.'
    exit 2
  fi
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    ui_fail 'Brak interaktywnego terminala do potwierdzenia deinstalacji. Uruchom ponownie z --yes.'
    exit 2
  fi

  local answer=''
  if ((purge_data)); then
    ui_warn 'Ta operacja usunie bazę PostgreSQL, konfigurację, dane, backupy i użytkownika systemowego cloudportal.'
    printf 'Wpisz USUN, aby potwierdzić pełne usunięcie: ' >/dev/tty
    IFS= read -r answer </dev/tty || true
    if [[ "$answer" != 'USUN' ]]; then
      ui_info 'Deinstalacja anulowana.'
      exit 0
    fi
    return 0
  fi

  printf 'Odinstalować runtime Cloudportal i zachować bazę oraz dane? [y/N] ' >/dev/tty
  IFS= read -r answer </dev/tty || true
  case "$answer" in
    y|Y|yes|YES|tak|TAK) return 0 ;;
    *) ui_info 'Deinstalacja anulowana.'; exit 0 ;;
  esac
}

verify_uninstall() {
  local leftovers=0 path
  local runtime_paths=(
    "$app_root"
    /usr/local/lib/cloudportal-updater
    /usr/local/sbin/cloudportal-backup
    /usr/local/sbin/cloudportal-restore
    /etc/nginx/conf.d/cloudportal-backed.conf
    /etc/systemd/system/cloudportal-api.service
    /etc/systemd/system/cloudportal-dispatcher.service
    /etc/systemd/system/cloudportal-worker@.service
    /etc/systemd/system/cloudportal-redis.service
    /etc/systemd/system/cloudportal-backup.service
    /etc/systemd/system/cloudportal-backup.timer
    /etc/systemd/system/cloudportal-updater.service
    /etc/systemd/system/cloudportal-updater.timer
    /etc/cron.d/cloudportal-auto-update
    /usr/local/sbin/cloudportal-auto-update
    /usr/local/lib/cloudportal-updater/cron-update.py
    /etc/logrotate.d/cloudportal-auto-update
  )

  for path in "${runtime_paths[@]}"; do
    if [[ -e "$path" || -L "$path" ]]; then
      ui_warn "Pozostałość po deinstalacji: $path"
      leftovers=1
    fi
  done

  if ((purge_data)); then
    for path in "$config" "$data" /var/backups/cloudportal-backed; do
      if [[ -e "$path" || -L "$path" ]]; then
        ui_warn "Pozostałość danych po purge: $path"
        leftovers=1
      fi
    done
    if id cloudportal >/dev/null 2>&1; then
      ui_warn 'Użytkownik systemowy cloudportal nadal istnieje.'
      leftovers=1
    fi
    if command -v runuser >/dev/null 2>&1 && command -v psql >/dev/null 2>&1 && id postgres >/dev/null 2>&1; then
      if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='cloudportal'" 2>/dev/null | grep -qx 1; then
        ui_warn 'Baza PostgreSQL cloudportal nadal istnieje.'
        leftovers=1
      fi
      if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='cloudportal'" 2>/dev/null | grep -qx 1; then
        ui_warn 'Rola PostgreSQL cloudportal nadal istnieje.'
        leftovers=1
      fi
    fi
  fi

  ((leftovers == 0)) || {
    ui_fail 'Deinstalacja pozostawiła elementy Cloudportal. Sprawdź komunikaty [WARN] powyżej.'
    return 1
  }
  ui_ok 'Weryfikacja deinstalacji zakończona pomyślnie.'
}

uninstall_cloudportal() {
  ui_stage 1 4 'Blokada i zatrzymanie usług'
  ui_info 'Przejmuję blokadę instalatora i zatrzymuję usługi Cloudportal.'
  acquire_install_lock
  auto_update_remove
  update_in_progress=0
  stop_cloudportal_application
  systemctl stop cloudportal-updater.timer cloudportal-updater.service >/dev/null 2>&1 || true

  if id cloudportal >/dev/null 2>&1; then
    pkill -TERM -u cloudportal >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -u cloudportal >/dev/null 2>&1 || true
  fi
  ui_ok 'Procesy aplikacji zostały zatrzymane.'

  ui_stage 2 4 'Usunięcie runtime i integracji systemowej'
  local worker_units=()
  mapfile -t worker_units < <(
    systemctl list-units --all --type=service --no-legend --no-pager 'cloudportal-worker@*.service' 2>/dev/null |
      awk '{print $1}' |
      grep -E '^cloudportal-worker@.+\.service$' || true
  )
  if ((${#worker_units[@]})); then
    systemctl disable --now "${worker_units[@]}" >/dev/null 2>&1 || true
  fi

  systemctl disable --now \
    cloudportal-api.service \
    cloudportal-dispatcher.service \
    cloudportal-redis.service \
    cloudportal-backup.timer \
    cloudportal-backup.service \
    cloudportal-updater.timer \
    cloudportal-updater.service >/dev/null 2>&1 || true

  rm -f \
    /etc/systemd/system/cloudportal-api.service \
    /etc/systemd/system/cloudportal-dispatcher.service \
    /etc/systemd/system/cloudportal-worker@.service \
    /etc/systemd/system/cloudportal-redis.service \
    /etc/systemd/system/cloudportal-backup.service \
    /etc/systemd/system/cloudportal-backup.timer \
    /etc/systemd/system/cloudportal-updater.service \
    /etc/systemd/system/cloudportal-updater.timer \
    /usr/local/sbin/cloudportal-backup \
    /usr/local/sbin/cloudportal-restore \
    /etc/nginx/conf.d/cloudportal-backed.conf

  systemctl daemon-reload
  systemctl reset-failed >/dev/null 2>&1 || true

  if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
  fi

  if [[ "$os_family" == rhel ]] && command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    if [[ "${previous_port:-}" =~ ^[0-9]{1,5}$ ]]; then
      firewall-cmd --permanent --remove-port="${previous_port}/tcp" >/dev/null 2>&1 || true
      firewall-cmd --reload >/dev/null 2>&1 || true
    fi
  fi

  rm -rf "$app_root" /usr/local/lib/cloudportal-updater
  rm -f /run/cloudportal-install.ready.*
  ui_ok 'Runtime, jednostki systemd, helpery i konfiguracja Nginx zostały usunięte.'

  ui_stage 3 4 'Polityka danych'
  if ((purge_data)); then
    ui_warn 'PURGE DATA: usuwam lokalną bazę Cloudportal, konfigurację, dane i backupy.'
    if ! systemctl is-active --quiet postgresql.service 2>/dev/null; then
      ui_info 'Uruchamiam PostgreSQL na czas bezpiecznego usunięcia bazy Cloudportal.'
      systemctl start postgresql.service
    fi
    runuser -u postgres -- dropdb --force --if-exists cloudportal >/dev/null
    runuser -u postgres -- dropuser --if-exists cloudportal >/dev/null
    rm -rf "$config" "$data" /var/backups/cloudportal-backed
    userdel cloudportal >/dev/null 2>&1 || true
    ui_ok 'Konfiguracja, dane, backupy i użytkownik systemowy zostały usunięte.'
  else
    ui_ok 'Baza, konfiguracja i dane aplikacji zostały zachowane.'
    ui_info "Konfiguracja: $config"
    ui_info "Dane: $data"
    ui_info 'Pełny reset wymaga: --uninstall --purge-data'
  fi

  ui_stage 4 4 'Weryfikacja'
  verify_uninstall

  ui_header 'Podsumowanie'
  ui_ok 'Cloudportal-backed został odinstalowany.'
  ui_info 'Pakiety współdzielone PostgreSQL, Redis/Valkey, Nginx, Terraform i Ansible nie są automatycznie usuwane.'
  if ((purge_data)); then
    ui_info 'Dane Cloudportal: usunięte.'
  else
    ui_info 'Dane Cloudportal: zachowane.'
  fi
}


native_recovery_admin() {
  CURRENT_STAGE='recovery administratora'
  ui_header 'Cloudportal-backed — recovery administratora'

  local release
  release=$(readlink -f "$app_root/current" 2>/dev/null || true)
  [[ -n "$release" && -d "$release" && -x "$release/.venv/bin/python" ]] || {
    ui_fail "Brak aktywnego runtime w $app_root/current."
    return 1
  }
  [[ -r "$config/backend.env" ]] || { ui_fail "Brak konfiguracji: $config/backend.env"; return 1; }
  id cloudportal >/dev/null 2>&1 || { ui_fail 'Brak użytkownika systemowego cloudportal.'; return 1; }

  recovery_prepare_inputs

  local args=(-m app.recovery --username "$recovery_username" --password-stdin)
  [[ -z "$recovery_email" ]] || args+=(--email "$recovery_email")
  [[ -z "$recovery_project" ]] || args+=(--project "$recovery_project")

  local loader='import os,sys; from pathlib import Path; [os.environ.__setitem__(*line.split("=",1)) for line in Path(sys.argv[1]).read_text().splitlines() if line and not line.startswith("#")]; os.chdir(sys.argv[2]); os.execv(sys.argv[3], sys.argv[3:])'
  ui_info 'Tworzę lub odzyskuję lokalne konto Administrator i przypisuję je do projektu.'
  if ! printf '%s\n' "$recovery_password" | runuser -u cloudportal -- "$release/.venv/bin/python" -c "$loader"       "$config/backend.env" "$release" "$release/.venv/bin/python" "${args[@]}"; then
    unset recovery_password
    recovery_password=''
    ui_fail 'Recovery administratora nie powiodło się.'
    return 1
  fi
  unset recovery_password
  recovery_password=''

  ui_ok "Recovery zakończone. Konto: $recovery_username"
  ui_info "Projekt: ${recovery_project:-default}"
  ui_info 'Wszystkie wcześniejsze tokeny tego konta zostały unieważnione.'
}

if ((recovery_mode)); then
  acquire_install_lock
  native_recovery_admin
  drain_script_input
  exit 0
fi

if ((status_mode)); then
  show_status
  drain_script_input
  exit 0
fi

if ((uninstall_mode)); then
  CURRENT_STAGE='deinstalacja'
  ui_header 'Cloudportal-backed — deinstalacja'
  if ((purge_data)); then
    ui_warn 'Tryb PURGE: baza danych, konfiguracja, dane i backupy zostaną trwale usunięte.'
  else
    ui_info 'Runtime i integracje systemowe zostaną usunięte; baza, konfiguracja i dane zostaną zachowane.'
  fi
  uninstall_preflight
  confirm_uninstall
  uninstall_cloudportal
  drain_script_input
  exit 0
fi

gui_cancel() {
  dialog --clear </dev/tty 2>/dev/tty || true
  echo 'Installation cancelled.' >&2
  drain_script_input
  exit 0
}
gui_message() {
  dialog --title 'Cloudportal-backed installer' --msgbox "$1" 9 68 </dev/tty 2>/dev/tty
}
gui_input() {
  local label=$1 value=$2 result
  if ! result=$(dialog --stdout --title 'Cloudportal-backed installer' --inputbox "$label" 10 72 "$value" </dev/tty); then
    gui_cancel
  fi
  printf '%s' "$result"
}
if ((gui)); then
  [[ -r /dev/tty && -w /dev/tty ]] || { echo 'GUI mode requires an interactive TTY.' >&2; drain_script_input; exit 1; }
  if ! command -v dialog >/dev/null; then
    echo 'Installing dialog dependency for GUI mode...'
    case "$os_family" in
      debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y dialog
        ;;
      rhel)
        dnf install -y dialog
        ;;
    esac
  fi
  command -v dialog >/dev/null || { echo 'dialog could not be installed.' >&2; drain_script_input; exit 1; }

  while :; do
    backend_host=$(gui_input 'Hostname lub adres DNS backendu:' "$backend_host")
    valid_host "$backend_host" && break
    gui_message 'Nieprawidłowy hostname. Użyj liter, cyfr, kropek i myślników.'
  done
  while :; do
    backend_port=$(gui_input 'Port HTTPS backendu (1-65535; 6389, 8765 i 8766 są zarezerwowane):' "$backend_port")
    valid_port "$backend_port" && break
    gui_message 'Nieprawidłowy port. Dozwolone 1-65535 z wyjątkiem 6389, 8765 i 8766.'
  done
  while :; do
    workers=$(gui_input 'Liczba workerów (1-64):' "$workers")
    valid_workers "$workers" && break
    gui_message 'Nieprawidłowa liczba workerów. Dozwolone 1-64.'
  done

  if [[ "$backup_schedule" == true ]]; then
    backup_default=()
  else
    backup_default=(--defaultno)
  fi
  if dialog "${backup_default[@]}" --title 'Cloudportal-backed installer' --yesno 'Włączyć codzienny backup PostgreSQL?' 9 68 </dev/tty; then
    backup_schedule=true
  else
    rc=$?
    if ((rc == 1)); then backup_schedule=false; else gui_cancel; fi
  fi
  if [[ "$backup_schedule" == true ]]; then
    while :; do
      backup_retention_days=$(gui_input 'Retencja backupów w dniach (1-3650):' "$backup_retention_days")
      valid_retention "$backup_retention_days" && break
      gui_message 'Nieprawidłowa retencja. Dozwolone 1-3650 dni.'
    done
  fi

  summary="Host: $backend_host
Port HTTPS: $backend_port
Workery: $workers
Backup codzienny: $backup_schedule
Retencja backupu: $backup_retention_days dni
Git ref: $ref"
  if ! dialog --title 'Cloudportal-backed installer' --yesno "Sprawdź konfigurację:

$summary

Rozpocząć instalację?" 16 72 </dev/tty; then
    gui_cancel
  fi
  dialog --clear </dev/tty 2>/dev/tty || true
fi

ui_header 'Cloudportal-backed — instalacja'
ui_stage 1 7 'Pretest środowiska'

valid_host "$backend_host" || { ui_fail 'Nieprawidłowy host. Użyj nazwy DNS lub adresu bez schematu URL.'; exit 2; }
valid_port "$backend_port" || { ui_fail 'Nieprawidłowy port. Dozwolone 1-65535 z wyjątkiem 6389, 8765 i 8766.'; exit 2; }
valid_workers "$workers" || { ui_fail 'Nieprawidłowa liczba workerów. Dozwolone 1-64.'; exit 2; }
[[ "$backup_schedule" == true || "$backup_schedule" == false ]] || { ui_fail 'Nieprawidłowa opcja harmonogramu backupu.'; exit 2; }
valid_retention "$backup_retention_days" || { ui_fail 'Nieprawidłowa retencja backupu. Dozwolone 1-3650 dni.'; exit 2; }
backend_port=$((10#$backend_port))
workers=$((10#$workers))
backup_retention_days=$((10#$backup_retention_days))
[[ -z "$github_token_file" || -z "$github_config" ]] || { ui_fail 'Użyj tylko jednej opcji: --github-token-file albo --github-config.'; exit 2; }
[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != *..* ]] || { ui_fail 'Nieprawidłowy Git ref.'; exit 2; }
[[ -z "$update_channel_ref" || "$update_channel_ref" =~ ^[A-Za-z0-9._/-]+$ && "$update_channel_ref" != *..* ]] || { ui_fail 'Nieprawidłowy CLOUDPORTAL_UPDATE_CHANNEL_REF.'; exit 2; }
release_ref=${update_channel_ref:-$ref}
[[ -z "$cert_file" && -z "$cert_key" || -r "$cert_file" && -r "$cert_key" ]] || { ui_fail 'Podaj oba pliki TLS: --cert-file i --cert-key.'; exit 2; }
preflight_checks
ui_ok 'Pretest zakończony.'
install_progress 5 preflight 'Sprawdzono platformę i konfigurację.'

ui_info 'Sprawdzam blokadę instalatora; aktywna poprzednia instalacja zostanie przejęta zgodnie z ustawieniem takeover.'
acquire_install_lock
ui_ok 'Blokada instalatora przejęta.'

ui_stage 2 7 'Pakiety systemowe i zależności'
install_progress 10 packages 'Instalowanie i aktualizowanie zależności systemowych.'
ui_info 'Aktualizuję repozytoria pakietów i instaluję PostgreSQL, Redis/Valkey, Nginx, Python oraz narzędzia systemowe.'
case "$os_family" in
  debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl unzip python3 python3-venv python3-dev build-essential libpq-dev postgresql redis-server nginx openssl sshpass openssh-client qemu-utils
    ;;
  rhel)
    if ((rhel_major == 9)); then
      python_packages=(python3.12 python3.12-pip python3.12-devel)
    else
      python_packages=(python3 python3-pip python3-devel)
    fi
    dnf install -y ca-certificates curl unzip "${python_packages[@]}" gcc gcc-c++ make redhat-rpm-config libpq-devel postgresql-server "$key_value_package" nginx openssl sshpass openssh-clients qemu-img policycoreutils-python-utils
    [[ -s /var/lib/pgsql/data/PG_VERSION ]] || postgresql-setup --initdb
    ;;
esac
python_binary=$(command -v "$python_command") || { ui_fail "Po instalacji pakietów nadal brakuje: $python_command"; exit 1; }
key_value_binary=$(command -v "$key_value_command") || { ui_fail "Po instalacji pakietów nadal brakuje serwera Redis/Valkey: $key_value_command"; exit 1; }
nologin_shell=$(command -v nologin) || { ui_fail 'Brak powłoki nologin wymaganej dla użytkownika systemowego.'; exit 1; }
ui_ok 'Pakiety systemowe są gotowe.'

ui_stage 3 7 'Kod aplikacji i środowisko wykonawcze'
ui_info "Przygotowuję użytkownika cloudportal i katalogi w $app_root oraz $data."
getent passwd cloudportal >/dev/null || useradd --system --home-dir "$data" --create-home --shell "$nologin_shell" cloudportal
install -d -m 0755 "$app_root" "$app_root/releases"
install -d -m 0700 -o cloudportal -g cloudportal "$config" "$data" "$data/workspaces" "$data/runs"
install -d -m 0700 "$config/tls"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl_args=(-fsSL --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 180 --retry 3)
if [[ -n "$github_config" ]]; then
  [[ -r "$github_config" && "$(stat -c %a "$github_config")" == 600 ]] || { ui_fail 'Plik --github-config musi istnieć, być czytelny i mieć tryb 600.'; exit 1; }
  curl_args+=(--config "$github_config")
fi
if [[ -n "$github_token_file" ]]; then
  [[ -r "$github_token_file" && "$(stat -c %a "$github_token_file")" == 600 ]] || { ui_fail 'Plik --github-token-file musi istnieć, być czytelny i mieć tryb 600.'; exit 1; }
  github_token=$(tr -d '\r\n' < "$github_token_file")
  [[ "$github_token" =~ ^[A-Za-z0-9._-]{20,512}$ ]] || { ui_fail 'Token GitHub ma nieprawidłowy format. Sprawdź plik i uprawnienie Contents: read.'; exit 1; }
  printf 'header = "Authorization: Bearer %s"\n' "$github_token" > "$tmp/curl.conf"
  unset github_token
  curl_args+=(--config "$tmp/curl.conf")
fi
ui_info "Pobieram kod źródłowy z GitHub: $repo @ $ref"
install_progress 25 download 'Pobieranie źródeł wybranej wersji.'
release_sha=${CLOUDPORTAL_RELEASE_SHA:-}
effective_tarball_url=$(curl "${curl_args[@]}" -w '%{url_effective}' "https://api.github.com/repos/$repo/tarball/$ref" -o "$tmp/source.tar.gz") || {
  ui_fail 'Nie udało się pobrać kodu. Dla prywatnego repo sprawdź token Contents: read, DNS, proxy i dostęp do api.github.com.'
  exit 1
}
if [[ -z "$release_sha" ]]; then
  candidate_sha=${effective_tarball_url##*/}
  [[ "$candidate_sha" =~ ^[0-9a-fA-F]{40}$ ]] && release_sha=${candidate_sha,,} || true
fi
mkdir "$tmp/source"
tar -xzf "$tmp/source.tar.gz" -C "$tmp/source" --strip-components=1 --no-same-owner
[[ -f "$tmp/source/app/main.py" && -f "$tmp/source/requirements.txt" ]] || { ui_fail 'Pobrane archiwum nie wygląda jak Cloudportal-backed. Sprawdź --ref oraz dostęp do repozytorium.'; exit 1; }
archive_sha=$(sha256sum "$tmp/source.tar.gz" | awk '{print $1}')
release=$(mktemp -d "$app_root/releases/$(date -u +%Y%m%dT%H%M%SZ)-${archive_sha:0:12}-XXXXXX")
cp -a "$tmp/source/." "$release/"
chmod -R go-w "$release"
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
"$python_binary" - "$release/.cloudportal-release.json" "$repo" "$release_ref" "$release_sha" "$archive_sha" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    'repository': sys.argv[2],
    'ref': sys.argv[3],
    'commit_sha': sys.argv[4],
    'archive_sha256': sys.argv[5],
    'installed_at': datetime.now(timezone.utc).isoformat(),
}, indent=2) + '\n')
PY
install_progress 35 python 'Tworzenie środowiska Python i instalowanie zależności aplikacji.'
"$python_binary" -m venv "$release/.venv"
ui_info 'Tworzę Python venv i instaluję zależności backendu oraz Ansible.'
"$release/.venv/bin/pip" install --disable-pip-version-check -r "$release/requirements.txt" 'ansible>=10,<13' 'pywinrm>=0.5,<1'
chmod -R a+rX "$release/.venv"
# Ansible and Terraform are executable by the runtime user, never run as root.
ln -sfn "$release/.venv/bin/ansible-playbook" /usr/local/bin/ansible-playbook
if ! command -v terraform >/dev/null; then
  ui_info 'Terraform nie jest zainstalowany — pobieram zweryfikowany release HashiCorp.'
  terraform_version=1.13.5
  terraform_file="terraform_${terraform_version}_linux_${arch}.zip"
  curl -fsSL --proto '=https' --tlsv1.2 "https://releases.hashicorp.com/terraform/$terraform_version/$terraform_file" -o "$tmp/$terraform_file"
  curl -fsSL --proto '=https' --tlsv1.2 "https://releases.hashicorp.com/terraform/$terraform_version/terraform_${terraform_version}_SHA256SUMS" -o "$tmp/sums"
  (cd "$tmp" && awk -v file="$terraform_file" '$2 == file { print }' sums > check && test -s check && sha256sum -c check)
  unzip -q "$tmp/$terraform_file" -d "$tmp/terraform"
  install -m 0755 "$tmp/terraform/terraform" /usr/local/bin/terraform
fi
ui_ok "Runtime aplikacji przygotowany w $release."

ui_stage 4 7 'PostgreSQL i Redis/Valkey'
ui_info 'Uruchamiam PostgreSQL i przygotowuję rolę oraz bazę cloudportal.'
systemctl enable --now postgresql
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='cloudportal'" | grep -qx 1; then
  runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole cloudportal
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='cloudportal'" | grep -qx 1; then
  runuser -u postgres -- createdb --owner=cloudportal cloudportal
fi
# Dedicated Redis instance bound to loopback; random password is generated once.
if [[ ! -f "$config/backend.env" ]]; then
  redis_password=$(openssl rand -hex 32)
  cat > "$config/backend.env" <<EOF
CP_DATABASE_URL=postgresql+psycopg:///cloudportal?host=/var/run/postgresql
CP_REDIS_URL=redis://:$redis_password@127.0.0.1:6389/0
CP_MASTER_KEY_FILE=$config/master.key
CP_DATA_DIR=$data
CP_WORKER_COUNT=$workers
CP_BACKUP_SCHEDULE_ENABLED=$backup_schedule
CP_BACKUP_RETENTION_DAYS=$backup_retention_days
CP_INSTALL_MODE=systemd
CP_PUBLIC_HOST=$backend_host
CP_HTTPS_PORT=$backend_port
CP_INSTANCE_BACKUP_DOWNLOAD_RETENTION_HOURS=24
CP_INSTANCE_BACKUP_MAX_UPLOAD_BYTES=10737418240
CP_APPLIANCE_MAX_UPLOAD_BYTES=21474836480
EOF
  cat > "$config/redis.conf" <<EOF
bind 127.0.0.1
port 6389
protected-mode yes
requirepass $redis_password
appendonly yes
dir $data/redis
logfile ""
EOF
  unset redis_password
fi
chown cloudportal:cloudportal "$config/backend.env" "$config/redis.conf"
chmod 0600 "$config/backend.env" "$config/redis.conf"
# Update only nonsecret runtime settings; preserve credentials and master key.
sed -i "s/^CP_WORKER_COUNT=.*/CP_WORKER_COUNT=$workers/" "$config/backend.env"
if grep -q '^CP_BACKUP_SCHEDULE_ENABLED=' "$config/backend.env"; then
  sed -i "s/^CP_BACKUP_SCHEDULE_ENABLED=.*/CP_BACKUP_SCHEDULE_ENABLED=$backup_schedule/" "$config/backend.env"
else
  printf 'CP_BACKUP_SCHEDULE_ENABLED=%s\n' "$backup_schedule" >> "$config/backend.env"
fi
if grep -q '^CP_BACKUP_RETENTION_DAYS=' "$config/backend.env"; then
  sed -i "s/^CP_BACKUP_RETENTION_DAYS=.*/CP_BACKUP_RETENTION_DAYS=$backup_retention_days/" "$config/backend.env"
else
  printf 'CP_BACKUP_RETENTION_DAYS=%s\n' "$backup_retention_days" >> "$config/backend.env"
fi
for runtime_pair in "CP_INSTALL_MODE=systemd" "CP_PUBLIC_HOST=$backend_host" "CP_HTTPS_PORT=$backend_port"; do
  runtime_key=${runtime_pair%%=*}
  runtime_value=${runtime_pair#*=}
  if grep -q "^$runtime_key=" "$config/backend.env"; then
    sed -i "s|^$runtime_key=.*|$runtime_key=$runtime_value|" "$config/backend.env"
  else
    printf '%s=%s\n' "$runtime_key" "$runtime_value" >> "$config/backend.env"
  fi
done
grep -q '^CP_INSTANCE_BACKUP_DOWNLOAD_RETENTION_HOURS=' "$config/backend.env" || printf 'CP_INSTANCE_BACKUP_DOWNLOAD_RETENTION_HOURS=24\n' >> "$config/backend.env"
grep -q '^CP_INSTANCE_BACKUP_MAX_UPLOAD_BYTES=' "$config/backend.env" || printf 'CP_INSTANCE_BACKUP_MAX_UPLOAD_BYTES=10737418240\n' >> "$config/backend.env"
grep -q '^CP_APPLIANCE_MAX_UPLOAD_BYTES=' "$config/backend.env" || printf 'CP_APPLIANCE_MAX_UPLOAD_BYTES=21474836480\n' >> "$config/backend.env"
install_progress 42 updater 'Konfigurowanie niezależnego serwisu aktualizacji.'
for token_file in "$config/updater.token" "$config/updater-status.token"; do
  if [[ ! -s "$token_file" ]]; then
    openssl rand -hex 32 > "$token_file"
  fi
  chown root:cloudportal "$token_file"
  chmod 0640 "$token_file"
done
persistent_github_token=''
persistent_github_config=''
if [[ -n "$github_token_file" ]]; then
  persistent_github_token="$config/github.token"
  if [[ "$github_token_file" != "$persistent_github_token" ]]; then
    install -m 0600 "$github_token_file" "$persistent_github_token"
  fi
  chown root:root "$persistent_github_token"
fi
if [[ -n "$github_config" ]]; then
  persistent_github_config="$config/github.curl.conf"
  if [[ "$github_config" != "$persistent_github_config" ]]; then
    install -m 0600 "$github_config" "$persistent_github_config"
  fi
  chown root:root "$persistent_github_config"
fi
updater_config="$config/updater.json"
"$python_binary" - "$updater_config" "$release_ref" "$persistent_github_token" "$persistent_github_config" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text()) if path.exists() else {}
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
data.setdefault('enabled', False)
data.setdefault('interval_hours', 24)
data.setdefault('require_ci', True)
data.setdefault('ci_workflow', 'Backend CI')
data.setdefault('ci_wait_minutes', 45)
data.setdefault('candidate_validation', True)
data.setdefault('runtime_preflight', True)
data['ref'] = sys.argv[2]
if sys.argv[3]:
    data['github_token_file'] = sys.argv[3]
else:
    data.setdefault('github_token_file', '')
if sys.argv[4]:
    data['github_config'] = sys.argv[4]
else:
    data.setdefault('github_config', '')
path.write_text(json.dumps(data, indent=2) + '\n')
os.chmod(path, 0o640)
PY
chown root:cloudportal "$updater_config"
install -d -m 0755 /usr/local/lib/cloudportal-updater
install -m 0755 "$release/scripts/update-service.py" /usr/local/lib/cloudportal-updater/update-service.py
install -d -m 0700 -o root -g root "$data/update"
install -d -m 0700 -o cloudportal -g cloudportal "$data/redis"
cat > /etc/systemd/system/cloudportal-redis.service <<EOF
[Unit]
Description=Cloud Portal dedicated Redis
After=network.target
[Service]
User=cloudportal
Group=cloudportal
ExecStart=$key_value_binary $config/redis.conf
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$data/redis
PrivateTmp=true
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
if [[ "$os_family" == rhel ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
  # Keep SELinux enforcing: label the dedicated Redis/Valkey files and ports,
  # and permit Nginx to reach the loopback API reverse-proxy target.
  ensure_selinux_port() {
    local type=$1 protocol=$2 port=$3
    if semanage port -a -t "$type" -p "$protocol" "$port" 2>/dev/null; then
      return
    fi
    if LC_ALL=C semanage port -l | awk -v type="$type" -v protocol="$protocol" -v port="$port" '
      $1 == type && $2 == protocol {
        for (i = 3; i <= NF; i++) {
          gsub(/,/, "", $i)
          split($i, range, "-")
          end = range[2] ? range[2] : range[1]
          if (port >= range[1] && port <= end) found = 1
        }
      }
      END { exit !found }
    '; then
      return
    fi
    echo "SELinux port $protocol/$port is assigned to a conflicting type." >&2
    exit 1
  }
  semanage fcontext -a -t redis_conf_t "$config/redis.conf" 2>/dev/null || semanage fcontext -m -t redis_conf_t "$config/redis.conf"
  semanage fcontext -a -t redis_var_lib_t "$data/redis(/.*)?" 2>/dev/null || semanage fcontext -m -t redis_var_lib_t "$data/redis(/.*)?"
  semanage fcontext -a -t cert_t "$config/tls(/.*)?" 2>/dev/null || semanage fcontext -m -t cert_t "$config/tls(/.*)?"
  ensure_selinux_port redis_port_t tcp 6389
  ensure_selinux_port http_port_t tcp "$backend_port"
  restorecon -R "$config/tls" "$data/redis"
  restorecon "$config/redis.conf"
  setsebool -P httpd_can_network_connect 1
fi
ui_ok 'Warstwa danych i dedykowany Redis/Valkey są skonfigurowane.'

ui_stage 5 7 'Migracje i usługi Cloudportal'
ui_info 'Tworzę klucz szyfrujący, wykonuję migracje bazy i generuję jednostki systemd.'
# No sourcing of secret env files as shell code.
run_backend() {
  runuser -u cloudportal -- "$release/.venv/bin/python" - "$config/backend.env" "$release" "$@" <<'PY'
import os, sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text().splitlines():
    if line and not line.startswith('#'):
        key, value = line.split('=', 1)
        os.environ[key] = value
os.chdir(sys.argv[2])
os.execv(sys.argv[3], sys.argv[3:])
PY
}
install_progress 50 database 'Aktualizowanie schematu bazy danych.'
run_backend "$release/.venv/bin/python" -m app.bootstrap --key-only
run_backend "$release/.venv/bin/alembic" upgrade head
install_progress 62 systemd 'Przygotowywanie jednostek systemd aplikacji.'
for service in api worker@ dispatcher; do
  write_paths="$data"
  case "$service" in
    api) command="$release/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --proxy-headers --forwarded-allow-ips 127.0.0.1";;
    worker@) command="$release/.venv/bin/python -m app.jobs.queue"; write_paths="$data $config";;
    dispatcher) command="$release/.venv/bin/python -m app.jobs.queue --dispatcher";;
  esac
  cat > "/etc/systemd/system/cloudportal-$service.service" <<EOF
[Unit]
Description=Cloudportal-backed $service
After=network-online.target postgresql.service cloudportal-redis.service
Wants=network-online.target
Requires=cloudportal-redis.service
[Service]
User=cloudportal
Group=cloudportal
WorkingDirectory=$release
EnvironmentFile=$config/backend.env
ExecStart=$command
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=control-group
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
ReadWritePaths=$write_paths
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
done
cat > /etc/systemd/system/cloudportal-updater.service <<EOF
[Unit]
Description=Cloudportal independent auto-update service
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=$python_binary /usr/local/lib/cloudportal-updater/update-service.py
Environment=CP_UPDATER_REPOSITORY=$repo
Environment=CP_UPDATER_PORT=8766
Restart=always
RestartSec=3
PrivateTmp=true
ProtectHome=read-only
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
ui_ok 'Migracje i definicje usług aplikacji są gotowe.'

ui_stage 6 7 'TLS i reverse proxy'
install_progress 72 tls 'Weryfikowanie konfiguracji TLS.'
ui_info "Konfiguruję certyfikat TLS oraz Nginx dla https://$backend_host:$backend_port."
tls_source_file="$config/tls/certificate-source"
tls_source=''
tls_certificate_changed=0

certificate_is_self_signed() {
  local subject issuer
  subject=$(openssl x509 -in "$1" -noout -subject -nameopt RFC2253 2>/dev/null) || return 1
  issuer=$(openssl x509 -in "$1" -noout -issuer -nameopt RFC2253 2>/dev/null) || return 1
  [[ "${subject#subject=}" == "${issuer#issuer=}" ]]
}

certificate_matches_host() {
  if [[ "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    openssl x509 -in "$1" -noout -checkip "$backend_host" >/dev/null 2>&1
  else
    openssl x509 -in "$1" -noout -checkhost "$backend_host" >/dev/null 2>&1
  fi
}

managed_certificate_matches_host() {
  local expected
  if [[ "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    expected="IP Address:$backend_host"
  else
    expected="DNS:$backend_host"
  fi
  openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null \
    | tail -n +2 \
    | tr ',' '\n' \
    | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
    | grep -Fxq "$expected"
}

certificate_key_matches() {
  local cert_public key_public
  cert_public=$(openssl x509 -in "$1" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256 2>/dev/null) || return 1
  key_public=$(openssl pkey -in "$2" -pubout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256 2>/dev/null) || return 1
  [[ -n "$cert_public" && "$cert_public" == "$key_public" ]]
}

generate_managed_tls_certificate() {
  local san="DNS:$backend_host"
  [[ ! "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || san="IP:$backend_host"
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -keyout "$config/tls/server.key" -out "$config/tls/server.crt" \
    -subj "/CN=$backend_host" -addext "subjectAltName=$san" >/dev/null 2>&1
  chmod 0600 "$config/tls/server.crt" "$config/tls/server.key"
  tls_source='managed-self-signed'
  tls_certificate_changed=1
  printf '%s\n' "$tls_source" > "$tls_source_file"
  echo "A self-signed TLS certificate for $backend_host was generated. Trust server.crt on the PHP server, or install a CA-issued certificate."
}

if [[ -n "$cert_file" ]]; then
  install -m 0600 "$cert_file" "$config/tls/server.crt"
  install -m 0600 "$cert_key" "$config/tls/server.key"
  tls_source='custom'
  tls_certificate_changed=1
  printf '%s\n' "$tls_source" > "$tls_source_file"
elif [[ ! -f "$config/tls/server.crt" || ! -f "$config/tls/server.key" ]]; then
  generate_managed_tls_certificate
else
  [[ ! -r "$tls_source_file" ]] || tls_source=$(tr -d '\r\n' < "$tls_source_file")
  if [[ -z "$tls_source" ]] && certificate_is_self_signed "$config/tls/server.crt"; then
    # Installations created before certificate-source existed used the same self-signed path.
    tls_source='managed-self-signed'
    printf '%s\n' "$tls_source" > "$tls_source_file"
  fi

  if [[ "$tls_source" == 'managed-self-signed' ]]; then
    regenerate_tls=0
    openssl x509 -in "$config/tls/server.crt" -noout -checkend 86400 >/dev/null 2>&1 || regenerate_tls=1
    managed_certificate_matches_host "$config/tls/server.crt" || regenerate_tls=1
    [[ -z ${previous_host:-} || "$previous_host" == "$backend_host" ]] || regenerate_tls=1
    certificate_key_matches "$config/tls/server.crt" "$config/tls/server.key" || regenerate_tls=1
    if ((regenerate_tls)); then
      echo "Existing installer-managed TLS certificate is expired, mismatched, or does not cover $backend_host; regenerating it."
      generate_managed_tls_certificate
    fi
  else
    certificate_key_matches "$config/tls/server.crt" "$config/tls/server.key" || {
      echo 'Configured custom TLS certificate and key do not match. Pass a valid --cert-file/--cert-key pair.' >&2
      exit 1
    }
    openssl x509 -in "$config/tls/server.crt" -noout -checkend 300 >/dev/null 2>&1 || {
      echo 'Configured custom TLS certificate is expired or expires within 5 minutes.' >&2
      exit 1
    }
    certificate_matches_host "$config/tls/server.crt" || {
      echo "Configured custom TLS certificate does not cover host $backend_host. Use a matching --host or certificate." >&2
      exit 1
    }
  fi
fi
if [[ "$os_family" == rhel ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
  restorecon -R "$config/tls"
fi
install_progress 82 proxy 'Konfigurowanie reverse proxy i kanału podglądu aktualizacji.'
cat > /etc/nginx/conf.d/cloudportal-backed.conf <<EOF
server {
    listen $backend_port ssl;
    server_name $backend_host;
    ssl_certificate $config/tls/server.crt;
    ssl_certificate_key $config/tls/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 1m;
    client_body_timeout 15s;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location = /update-status {
        limit_except GET { deny all; }
        proxy_pass http://127.0.0.1:8766/status\$is_args\$args;
        proxy_set_header Host \$host;
        proxy_read_timeout 5s;
        proxy_connect_timeout 2s;
    }
    location ~ ^/api/v1/(?:appliances/ova-blueprints|instance-backups/upload)$ {
        limit_except POST { deny all; }
        client_max_body_size 100g;
        client_body_timeout 7200s;
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 7200s;
        proxy_send_timeout 7200s;
        proxy_connect_timeout 5s;
    }
    location ~ ^/api/v1/console-sessions/[A-Za-z0-9_-]+/websocket$ {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_read_timeout 60s;
        proxy_connect_timeout 5s;
    }
}
EOF
nginx -t
ln -sfn "$release" "$app_root/current"
cat > /usr/local/sbin/cloudportal-backup <<'EOF'
#!/bin/sh
exec /opt/cloudportal-backed/current/.venv/bin/python /opt/cloudportal-backed/current/scripts/backend-backup.py "$@"
EOF
cat > /usr/local/sbin/cloudportal-restore <<'EOF'
#!/bin/sh
exec /opt/cloudportal-backed/current/.venv/bin/python /opt/cloudportal-backed/current/scripts/backend-restore.py "$@"
EOF
chmod 0755 /usr/local/sbin/cloudportal-backup
chmod 0750 /usr/local/sbin/cloudportal-restore
install -d -m 0700 -o cloudportal -g cloudportal /var/backups/cloudportal-backed
cat > /etc/systemd/system/cloudportal-backup.service <<EOF
[Unit]
Description=Cloudportal-backed database backup
After=postgresql.service
[Service]
Type=oneshot
User=cloudportal
Group=cloudportal
ExecStart=/usr/local/sbin/cloudportal-backup --retention-days $backup_retention_days
UMask=0077
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/backups/cloudportal-backed
EOF
cat > /etc/systemd/system/cloudportal-backup.timer <<'EOF'
[Unit]
Description=Daily Cloudportal-backed database backup
[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30m
[Install]
WantedBy=timers.target
EOF
ui_ok 'TLS i konfiguracja Nginx są gotowe.'

ui_stage 7 7 'Start usług i testy końcowe'
ui_info 'Włączam usługi, workery, backup timer i wykonuję healthcheck HTTP/HTTPS.'
printf 'host=%s\nport=%s\n' "$backend_host" "$backend_port" > "$config/public.conf"
install_progress 90 services 'Przełączanie usług na nową wersję.'
systemctl daemon-reload
systemctl enable --now cloudportal-redis
systemctl enable cloudportal-updater
if ((update_in_progress)); then
  systemctl is-active --quiet cloudportal-updater || systemctl start cloudportal-updater
else
  systemctl restart cloudportal-updater
fi
systemctl enable cloudportal-api cloudportal-dispatcher
systemctl restart cloudportal-api cloudportal-dispatcher
for ((i=1;i<=workers;i++)); do systemctl enable "cloudportal-worker@$i"; systemctl restart "cloudportal-worker@$i"; done
if [[ "$backup_schedule" == true ]]; then
  systemctl enable --now cloudportal-backup.timer
else
  systemctl disable --now cloudportal-backup.timer >/dev/null 2>&1 || true
fi
systemctl enable --now nginx
if ((tls_certificate_changed)); then
  # A graceful reload may briefly leave an old TLS worker serving the previous certificate.
  # Restart only when certificate material changed; ordinary reinstalls keep a zero-downtime reload.
  systemctl restart nginx
else
  systemctl reload nginx
fi
if [[ "$os_family" == rhel ]] && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="$backend_port/tcp"
  firewall-cmd --reload
fi
install_progress 96 healthcheck 'Sprawdzanie stanu nowej wersji.'
ready=0
api_health_error="$tmp/api-health.err"
: > "$api_health_error"
for ((attempt=1;attempt<=60;attempt++)); do
  if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/v1/health       > "$tmp/health.json" 2> "$api_health_error"; then
    ready=1
    break
  fi

  # Nie spamuj oczekiwanymi "connection refused" podczas startu uvicorn.
  if ! systemctl is-active --quiet cloudportal-api.service; then
    ui_warn "API nie jest aktywne po restarcie (próba $attempt/60)."
    break
  fi

  if ((attempt == 1)); then
    ui_info 'API startuje — oczekuję na port 127.0.0.1:8765...'
  elif ((attempt % 10 == 0)); then
    ui_info "Nadal czekam na API: próba $attempt/60."
  fi
  sleep 2
done
((ready == 1)) || {
  ui_fail 'Lokalny healthcheck API nie przeszedł. Token bootstrap nie został wygenerowany.'
  if [[ -s "$api_health_error" ]]; then
    ui_info "Ostatni błąd curl: $(tail -n 1 "$api_health_error")"
  fi
  ui_info 'Stan cloudportal-api:'
  systemctl --no-pager --full status cloudportal-api.service || true
  ui_info 'Ostatnie logi cloudportal-api:'
  journalctl --no-pager -u cloudportal-api.service -n 80 || true
  ui_info 'Sprawdź również: systemctl status cloudportal-dispatcher cloudportal-worker@1'
  exit 1
}
tls_health_curl_args=(-fsS --noproxy '*' --connect-timeout 5 --max-time 15)
if [[ "$tls_source" == 'managed-self-signed' ]] || certificate_is_self_signed "$config/tls/server.crt"; then
  # Pin the exact local self-signed certificate. Do not disable TLS verification.
  tls_health_curl_args+=(--cacert "$config/tls/server.crt")
fi
tls_ready=0
for ((attempt=0;attempt<10;attempt++)); do
  if curl "${tls_health_curl_args[@]}" --resolve "$backend_host:$backend_port:127.0.0.1" \
    "https://$backend_host:$backend_port/api/v1/health" > "$tmp/tls-health.json" 2> "$tmp/tls-health.err"; then
    tls_ready=1
    break
  fi
  sleep 1
done
if ((tls_ready != 1)); then
  cat "$tmp/tls-health.err" >&2
  ui_fail "HTTPS healthcheck nie przeszedł dla https://$backend_host:$backend_port. Lokalne API działa, ale Nginx/TLS nie jest poprawny dla skonfigurowanego hosta."
  ui_info 'Token bootstrap nie został wygenerowany.'
  ui_info 'Sprawdź certyfikat, SAN/CN, port Nginx oraz zaufanie CA.'
  if [[ "$tls_source" == 'custom' ]] && ! certificate_is_self_signed "$config/tls/server.crt"; then
    ui_info 'Dla prywatnego CA zainstaluj certyfikat CA w systemowym trust store i uruchom instalator ponownie.'
  fi
  exit 1
fi
ui_ok 'Healthcheck HTTP i HTTPS zakończony pomyślnie.'
# Secrets are created only after services are healthy, printed only here and never written to logs/files.
ui_info 'Finalizuję bootstrap administratora. Nowy sekret/token, jeżeli powstanie, jest wyświetlany tylko raz.'
install_progress 99 bootstrap 'Finalizowanie konfiguracji aplikacji.'
run_backend "$release/.venv/bin/python" -m app.bootstrap --url "https://$backend_host:$backend_port"
install_progress 100 complete 'Instalacja lub aktualizacja zakończona pomyślnie.'

if ((update_in_progress)); then
  updater_reload_unit="cloudportal-updater-reload-$"
  if systemd-run --quiet --collect --unit="$updater_reload_unit" --on-active=8s \
      /bin/systemctl restart cloudportal-updater.service >/dev/null 2>&1; then
    ui_info 'Nowa wersja serwisu updatera zostanie aktywowana po zakończeniu bieżącej sesji.'
  else
    ui_warn 'Nie udało się zaplanować automatycznego restartu updatera; aktualizacja aplikacji jest zakończona.'
  fi
fi

ui_header 'Podsumowanie'
ui_ok 'Instalacja Cloudportal-backed zakończona.'
ui_info "Panel: https://$backend_host:$backend_port/ui/"
ui_info "Runtime: $release"
ui_info "Konfiguracja: $config"
ui_info "Dane: $data"
ui_info "Workery: $workers"
if [[ "$backup_schedule" == true ]]; then
  ui_info "Backup: włączony, retencja $backup_retention_days dni"
else
  ui_info 'Backup: timer wyłączony'
fi
ui_info 'Status: sudo ./install.sh --status'
ui_info 'Logi: journalctl -u cloudportal-api -u cloudportal-dispatcher -u cloudportal-worker@1 -n 100 --no-pager'