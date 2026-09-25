"""One-time, base-checked installer edit; removed before handing off the PR."""
from pathlib import Path
import hashlib

path = Path('install.sh')
original = path.read_bytes()
blob = hashlib.sha1(b'blob ' + str(len(original)).encode() + b'\0' + original).hexdigest()
if blob != '625f6ca91e8a509d0501dd43d6919a84f2660eeb':
    raise SystemExit('Installer changed; refuse to patch an unreviewed base: ' + blob)
text = original.decode('utf-8')


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Expected one installer anchor: ' + repr(old[:100]))
    text = text.replace(old, new, 1)


replace('Konfiguracja:\n', '''Automatyczne aktualizacje (cron, istniejąca instalacja):
  --enable-auto-update        Włącz cron; domyślnie co 12 godzin. Nie reinstaluje aplikacji teraz.
  --disable-auto-update       Usuń wyłącznie harmonogram cron Cloudportal i jego helper.
  --auto-update-status        Pokaż harmonogram cron bez zmiany instalacji.
  --auto-update-interval N    Interwał z --enable-auto-update: 1,2,3,4,6,8,12,24 godziny.
                              Używa zapisanych ustawień updatera; --docker wybiera stack Docker.

Konfiguracja:
''')
replace('  sudo ./install.sh --status\n', '''  sudo ./install.sh --status
  sudo ./install.sh --enable-auto-update --auto-update-interval 12
  sudo ./install.sh --docker --enable-auto-update --auto-update-interval 12
  sudo ./install.sh --auto-update-status
  sudo ./install.sh --disable-auto-update
''')
replace("backup_retention_days=''\n", "backup_retention_days=''\nauto_update_action=''\nauto_update_mode=0\nauto_update_interval=12\nauto_update_interval_explicit=0\n")
replace('|--backup-retention-days|--recovery-username', '|--backup-retention-days|--auto-update-interval|--recovery-username')
replace('--backup-retention-days) backup_retention_days=$2;;', '--backup-retention-days) backup_retention_days=$2;;\n        --auto-update-interval) auto_update_interval=$2; auto_update_interval_explicit=1;;')
replace('    --enable-backups) backup_schedule=true; shift;;', '''    --enable-auto-update|--disable-auto-update|--auto-update-status)
      ((auto_update_mode == 0)) || { ui_fail 'Wybierz tylko jedną operację auto-update.'; exit 2; }
      auto_update_mode=1
      case "$1" in
        --enable-auto-update) auto_update_action=enable;;
        --disable-auto-update) auto_update_action=disable;;
        --auto-update-status) auto_update_action=status;;
      esac
      shift;;
    --enable-backups) backup_schedule=true; shift;;''')
replace('done\n\ninteractive_action_menu() {', '''done

# Unattended/updater runs must never terminate a concurrent manual installer.
if ((update_in_progress)); then
  takeover_running_install=0
fi

interactive_action_menu() {''')
replace('  [10] Recovery password / konto Administrator — Docker\n', '''  [10] Recovery password / konto Administrator — Docker
  [11] Włącz automatyczne aktualizacje cron — systemd
  [12] Włącz automatyczne aktualizacje cron — Docker
  [13] Wyłącz automatyczne aktualizacje cron
  [14] Status automatycznych aktualizacji cron
''')
replace('Wybierz operację [0-10]: ', 'Wybierz operację [0-14]: ')
replace('      0)\n', '''      11|12)
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
''')
replace('Wpisz cyfrę od 0 do 10.', 'Wpisz numer od 0 do 14.')
replace('mode_count=$((status_mode + uninstall_mode + check_platform + recovery_mode))', 'mode_count=$((status_mode + uninstall_mode + check_platform + recovery_mode + auto_update_mode))')
replace('albo --recovery-admin.\'; exit 2; }', 'albo operację --recovery-admin/auto-update.\'; exit 2; }')
replace('if ((recovery_mode == 0)); then\n', '''((gui == 0 || auto_update_mode == 0)) || { ui_fail '--gui nie łączy się z zarządzaniem cron.'; exit 2; }
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
''')
replace('if ((uninstall_mode || status_mode || docker_mode || recovery_mode)); then', 'if ((uninstall_mode || status_mode || docker_mode || recovery_mode || auto_update_mode)); then')
replace('if ((uninstall_mode || status_mode || recovery_mode)); then', 'if ((uninstall_mode || status_mode || recovery_mode || auto_update_mode)); then')
replace('if ((status_mode == 0 || (docker_mode == 1 && status_mode == 1 && docker_auto_repair == 1))); then', 'if [[ "$auto_update_action" != status ]] && ((status_mode == 0 || (docker_mode == 1 && status_mode == 1 && docker_auto_repair == 1))); then')
replace('docker_root=/opt/cloudportal-backed-docker\n', Path('scripts/_cron_update_block.sh').read_text(encoding='utf-8') + 'docker_root=/opt/cloudportal-backed-docker\n')
replace("  ui_header 'Cloudportal-backed — status Docker'\n", "  ui_header 'Cloudportal-backed — status Docker'\n  auto_update_show_status\n")
replace("  ui_stage 1 3 'Zatrzymanie stacka'\n", "  ui_stage 1 3 'Zatrzymanie stacka'\n  auto_update_remove\n")
replace('if ((docker_mode)); then\n  if ((recovery_mode)); then', '''if ((auto_update_mode)); then
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
  if ((recovery_mode)); then''')
replace("  ui_header 'Cloudportal-backed — status'\n", "  ui_header 'Cloudportal-backed — status'\n  auto_update_show_status\n")
replace('    /etc/systemd/system/cloudportal-updater.timer\n  )', '''    /etc/systemd/system/cloudportal-updater.timer
    /etc/cron.d/cloudportal-auto-update
    /usr/local/sbin/cloudportal-auto-update
    /usr/local/lib/cloudportal-updater/cron-update.py
    /etc/logrotate.d/cloudportal-auto-update
  )''')
replace('  acquire_install_lock\n  update_in_progress=0\n', '  acquire_install_lock\n  auto_update_remove\n  update_in_progress=0\n')
path.write_text(text, encoding='utf-8')
print('Patched install.sh; all anchors matched the reviewed base.')
