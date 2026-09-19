#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Keep the complete installer at the repository root so curl | bash is standalone.
repo='chmajster/Cloudportal-backed'
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
gui=0
non_interactive=0
check_platform=0
update_in_progress=${CLOUDPORTAL_UPDATE_IN_PROGRESS:-0}
[[ "$update_in_progress" == 1 ]] || update_in_progress=0
install_progress() {
  local percent=$1 phase=$2 message=$3
  if ((update_in_progress)); then
    printf '::cloudportal-progress::%s::%s::%s\n' "$percent" "$phase" "$message"
  else
    printf '[%s%%] %s\n' "$percent" "$message"
  fi
}
while (($#)); do
  case "$1" in
    --host|--port|--workers|--ref|--github-token-file|--github-config|--cert-file|--cert-key|--backup-retention-days)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --host) backend_host=$2;; --port) backend_port=$2;; --workers) workers=$2;; --ref) ref=$2;;
        --github-token-file) github_token_file=$2;; --github-config) github_config=$2;; --cert-file) cert_file=$2;; --cert-key) cert_key=$2;; --backup-retention-days) backup_retention_days=$2;;
      esac
      shift 2;;
    --enable-backups) backup_schedule=true; shift;;
    --disable-backups) backup_schedule=false; shift;;
    --gui|-gui) gui=1; shift;;
    --non-interactive) non_interactive=1; shift;;
    --check-platform) check_platform=1; shift;;
    --help) echo 'install.sh [--host DNS_NAME] [--port 8443] [--workers 1] [--enable-backups|--disable-backups] [--backup-retention-days 14] [--gui|-gui] [--non-interactive] [--check-platform] [--ref REF] [--github-token-file FILE | --github-config FILE] [--cert-file PEM --cert-key PEM]'; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
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
    echo 'Supported: Ubuntu 24.04/26.04, Debian 12/13, RHEL 9/10 with systemd.' >&2
    drain_script_input
    exit 1
    ;;
esac
case "$(uname -m)" in x86_64) arch=amd64;; aarch64|arm64) arch=arm64;; *) echo 'Unsupported architecture.' >&2; exit 1;; esac
if ((check_platform)); then
  printf 'Supported platform: %s %s (%s, %s, %s, %s).\n' "$NAME" "$VERSION_ID" "$os_family" "$arch" "$key_value_package" "$python_command"
  drain_script_input
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo 'Run as root (sudo bash).' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'systemd is required.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'A running systemd host is required; use Docker Compose for a container.' >&2; exit 1; }
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

exec 9>/run/cloudportal-install.lock
flock -n 9 || { echo 'Another installation is running.' >&2; exit 1; }

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
  if ! result=$(dialog --stdout --title 'Cloudportal-backed installer' --inputbox "$label" 10 72 "$value" </dev/tty 9>&-); then
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
        apt-get update 9>&-
        apt-get install -y dialog 9>&-
        ;;
      rhel)
        dnf install -y dialog 9>&-
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
  if dialog "${backup_default[@]}" --title 'Cloudportal-backed installer' --yesno 'Włączyć codzienny backup PostgreSQL?' 9 68 </dev/tty 9>&-; then
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

Rozpocząć instalację?" 16 72 </dev/tty 9>&-; then
    gui_cancel
  fi
  dialog --clear </dev/tty 2>/dev/tty || true
fi

valid_host "$backend_host" || { echo 'Invalid host.' >&2; exit 2; }
valid_port "$backend_port" || { echo 'Invalid port.' >&2; exit 2; }
valid_workers "$workers" || { echo 'Invalid workers count.' >&2; exit 2; }
[[ "$backup_schedule" == true || "$backup_schedule" == false ]] || { echo 'Invalid backup schedule flag.' >&2; exit 2; }
valid_retention "$backup_retention_days" || { echo 'Invalid backup retention days.' >&2; exit 2; }
backend_port=$((10#$backend_port))
workers=$((10#$workers))
backup_retention_days=$((10#$backup_retention_days))
[[ -z "$github_token_file" || -z "$github_config" ]] || { echo 'Use either --github-token-file or --github-config.' >&2; exit 2; }
[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != *..* ]] || { echo 'Invalid git ref.' >&2; exit 2; }
[[ -z "$cert_file" && -z "$cert_key" || -r "$cert_file" && -r "$cert_key" ]] || { echo 'Both certificate files are required.' >&2; exit 2; }
install_progress 5 preflight 'Sprawdzono platformę i konfigurację.'
install_progress 10 packages 'Instalowanie i aktualizowanie zależności systemowych.'
case "$os_family" in
  debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl unzip python3 python3-venv python3-dev build-essential libpq-dev postgresql redis-server nginx openssl sshpass openssh-client
    ;;
  rhel)
    if ((rhel_major == 9)); then
      python_packages=(python3.12 python3.12-pip python3.12-devel)
    else
      python_packages=(python3 python3-pip python3-devel)
    fi
    dnf install -y ca-certificates curl unzip "${python_packages[@]}" gcc gcc-c++ make redhat-rpm-config libpq-devel postgresql-server "$key_value_package" nginx openssl sshpass openssh-clients policycoreutils-python-utils
    [[ -s /var/lib/pgsql/data/PG_VERSION ]] || postgresql-setup --initdb
    ;;
esac
python_binary=$(command -v "$python_command") || { echo "Missing Python command: $python_command" >&2; exit 1; }
key_value_binary=$(command -v "$key_value_command") || { echo "Missing Redis-compatible server command: $key_value_command" >&2; exit 1; }
nologin_shell=$(command -v nologin) || { echo 'Missing nologin shell.' >&2; exit 1; }
getent passwd cloudportal >/dev/null || useradd --system --home-dir "$data" --create-home --shell "$nologin_shell" cloudportal
install -d -m 0755 "$app_root" "$app_root/releases"
install -d -m 0700 -o cloudportal -g cloudportal "$config" "$data" "$data/workspaces" "$data/runs"
install -d -m 0700 "$config/tls"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl_args=(-fsSL --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 180 --retry 3)
if [[ -n "$github_config" ]]; then
  [[ -r "$github_config" && "$(stat -c %a "$github_config")" == 600 ]] || { echo 'GitHub curl configuration must be readable with mode 600.' >&2; exit 1; }
  curl_args+=(--config "$github_config")
fi
if [[ -n "$github_token_file" ]]; then
  [[ -r "$github_token_file" && "$(stat -c %a "$github_token_file")" == 600 ]] || { echo 'GitHub token file must be readable with mode 600.' >&2; exit 1; }
  github_token=$(tr -d '\r\n' < "$github_token_file")
  [[ "$github_token" =~ ^[A-Za-z0-9._-]{20,512}$ ]] || { echo 'Invalid GitHub token format.' >&2; exit 1; }
  printf 'header = "Authorization: Bearer %s"\n' "$github_token" > "$tmp/curl.conf"
  unset github_token
  curl_args+=(--config "$tmp/curl.conf")
fi
install_progress 25 download 'Pobieranie źródeł wybranej wersji.'
release_sha=${CLOUDPORTAL_RELEASE_SHA:-}
effective_tarball_url=$(curl "${curl_args[@]}" -w '%{url_effective}' "https://api.github.com/repos/$repo/tarball/$ref" -o "$tmp/source.tar.gz") || {
  echo 'Download failed. A private repository requires --github-token-file FILE; public repositories do not.' >&2; exit 1;
}
if [[ -z "$release_sha" ]]; then
  candidate_sha=${effective_tarball_url##*/}
  [[ "$candidate_sha" =~ ^[0-9a-fA-F]{40}$ ]] && release_sha=${candidate_sha,,} || true
fi
mkdir "$tmp/source"
tar -xzf "$tmp/source.tar.gz" -C "$tmp/source" --strip-components=1 --no-same-owner
[[ -f "$tmp/source/app/main.py" && -f "$tmp/source/requirements.txt" ]] || { echo 'Invalid release archive.' >&2; exit 1; }
archive_sha=$(sha256sum "$tmp/source.tar.gz" | awk '{print $1}')
release=$(mktemp -d "$app_root/releases/$(date -u +%Y%m%dT%H%M%SZ)-${archive_sha:0:12}-XXXXXX")
cp -a "$tmp/source/." "$release/"
chmod -R go-w "$release"
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
"$python_binary" - "$release/.cloudportal-release.json" "$repo" "$ref" "$release_sha" "$archive_sha" <<'PY'
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
"$release/.venv/bin/pip" install --disable-pip-version-check -r "$release/requirements.txt" 'ansible>=10,<13' 'pywinrm>=0.5,<1'
chmod -R a+rX "$release/.venv"
# Ansible and Terraform are executable by the runtime user, never run as root.
ln -sfn "$release/.venv/bin/ansible-playbook" /usr/local/bin/ansible-playbook
if ! command -v terraform >/dev/null; then
  terraform_version=1.13.5
  terraform_file="terraform_${terraform_version}_linux_${arch}.zip"
  curl -fsSL --proto '=https' --tlsv1.2 "https://releases.hashicorp.com/terraform/$terraform_version/$terraform_file" -o "$tmp/$terraform_file"
  curl -fsSL --proto '=https' --tlsv1.2 "https://releases.hashicorp.com/terraform/$terraform_version/terraform_${terraform_version}_SHA256SUMS" -o "$tmp/sums"
  (cd "$tmp" && awk -v file="$terraform_file" '$2 == file { print }' sums > check && test -s check && sha256sum -c check)
  unzip -q "$tmp/$terraform_file" -d "$tmp/terraform"
  install -m 0755 "$tmp/terraform/terraform" /usr/local/bin/terraform
fi
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
"$python_binary" - "$updater_config" "$ref" "$persistent_github_token" "$persistent_github_config" <<'PY'
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
  case "$service" in
    api) command="$release/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --proxy-headers --forwarded-allow-ips 127.0.0.1";;
    worker@) command="$release/.venv/bin/python -m app.jobs.queue";;
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
ReadWritePaths=$data
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
install_progress 72 tls 'Weryfikowanie konfiguracji TLS.'
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
for ((attempt=0;attempt<60;attempt++)); do
  if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/v1/health > "$tmp/health.json"; then ready=1; break; fi
  sleep 2
done
((ready == 1)) || { echo 'Healthcheck failed. Inspect systemctl status cloudportal-api cloudportal-dispatcher cloudportal-worker@1. Bootstrap token has not been generated.' >&2; exit 1; }
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
  echo "HTTPS healthcheck failed for https://$backend_host:$backend_port. The local API is healthy, but Nginx/TLS is not valid for the configured host. Bootstrap token has not been generated." >&2
  if [[ "$tls_source" == 'custom' ]] && ! certificate_is_self_signed "$config/tls/server.crt"; then
    echo 'For a private-CA certificate, install the issuing CA in the operating-system trust store before rerunning the installer.' >&2
  fi
  exit 1
fi
# Secrets are created only after services are healthy, printed only here and never written to logs/files.
install_progress 99 bootstrap 'Finalizowanie konfiguracji aplikacji.'
run_backend "$release/.venv/bin/python" -m app.bootstrap --url "https://$backend_host:$backend_port"
install_progress 100 complete 'Instalacja lub aktualizacja zakończona pomyślnie.'