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

