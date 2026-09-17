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
non_interactive=0
check_platform=0
while (($#)); do
  case "$1" in
    --host|--port|--workers|--ref|--github-token-file|--github-config|--cert-file|--cert-key)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --host) backend_host=$2;; --port) backend_port=$2;; --workers) workers=$2;; --ref) ref=$2;;
        --github-token-file) github_token_file=$2;; --github-config) github_config=$2;; --cert-file) cert_file=$2;; --cert-key) cert_key=$2;;
      esac
      shift 2;;
    --non-interactive) non_interactive=1; shift;;
    --check-platform) check_platform=1; shift;;
    --help) echo 'install.sh [--host DNS_NAME] [--port 8443] [--workers 1] [--non-interactive] [--check-platform] [--ref REF] [--github-token-file FILE | --github-config FILE] [--cert-file PEM --cert-key PEM]'; exit 0;;
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
fi
workers=${workers:-${previous_workers:-1}}
[[ "$backend_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$ ]] || { echo 'Invalid host.' >&2; exit 2; }
[[ "$backend_port" =~ ^[0-9]{1,5}$ ]] && ((10#$backend_port >= 1 && 10#$backend_port <= 65535)) || { echo 'Invalid port.' >&2; exit 2; }
[[ "$workers" =~ ^[0-9]{1,2}$ ]] && ((10#$workers >= 1 && 10#$workers <= 64)) || { echo 'Invalid workers count.' >&2; exit 2; }
backend_port=$((10#$backend_port))
workers=$((10#$workers))
((backend_port != 6389)) || { echo 'Port 6389 is reserved for the internal Redis/Valkey instance.' >&2; exit 2; }
[[ -z "$github_token_file" || -z "$github_config" ]] || { echo 'Use either --github-token-file or --github-config.' >&2; exit 2; }
[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != *..* ]] || { echo 'Invalid git ref.' >&2; exit 2; }
[[ -z "$cert_file" && -z "$cert_key" || -r "$cert_file" && -r "$cert_key" ]] || { echo 'Both certificate files are required.' >&2; exit 2; }
exec 9>/run/cloudportal-install.lock
flock -n 9 || { echo 'Another installation is running.' >&2; exit 1; }
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
  [[ "$github_token" =~ ^[A-Za-z0-9_]+$ ]] || { echo 'Invalid GitHub token format.' >&2; exit 1; }
  printf 'header = "Authorization: Bearer %s"\n' "$github_token" > "$tmp/curl.conf"
  unset github_token
  curl_args+=(--config "$tmp/curl.conf")
fi
curl "${curl_args[@]}" "https://api.github.com/repos/$repo/tarball/$ref" -o "$tmp/source.tar.gz" || {
  echo 'Download failed. A private repository requires --github-token-file FILE; public repositories do not.' >&2; exit 1;
}
mkdir "$tmp/source"
tar -xzf "$tmp/source.tar.gz" -C "$tmp/source" --strip-components=1 --no-same-owner
[[ -f "$tmp/source/app/main.py" && -f "$tmp/source/requirements.txt" ]] || { echo 'Invalid release archive.' >&2; exit 1; }
release=$(mktemp -d "$app_root/releases/$(date -u +%Y%m%dT%H%M%SZ)-$(sha256sum "$tmp/source.tar.gz" | cut -c1-12)-XXXXXX")
cp -a "$tmp/source/." "$release/"
chmod -R go-w "$release"
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
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
# Update only nonsecret expected worker count; preserve credentials and master key.
sed -i "s/^CP_WORKER_COUNT=.*/CP_WORKER_COUNT=$workers/" "$config/backend.env"
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
run_backend "$release/.venv/bin/python" -m app.bootstrap --key-only
run_backend "$release/.venv/bin/alembic" upgrade head
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
if [[ -n "$cert_file" ]]; then
  install -m 0600 "$cert_file" "$config/tls/server.crt"
  install -m 0600 "$cert_key" "$config/tls/server.key"
elif [[ ! -f "$config/tls/server.crt" ]]; then
  san="DNS:$backend_host"
  [[ ! "$backend_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || san="IP:$backend_host"
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 -keyout "$config/tls/server.key" -out "$config/tls/server.crt" -subj "/CN=$backend_host" -addext "subjectAltName=$san" >/dev/null 2>&1
  echo 'A self-signed TLS certificate was generated. Trust server.crt on the PHP server, or install a CA-issued certificate.'
fi
if [[ "$os_family" == rhel ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
  restorecon -R "$config/tls"
fi
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
printf 'host=%s\nport=%s\n' "$backend_host" "$backend_port" > "$config/public.conf"
systemctl daemon-reload
systemctl enable --now cloudportal-redis
systemctl enable cloudportal-api cloudportal-dispatcher
systemctl restart cloudportal-api cloudportal-dispatcher
for ((i=1;i<=workers;i++)); do systemctl enable "cloudportal-worker@$i"; systemctl restart "cloudportal-worker@$i"; done
systemctl enable --now nginx
systemctl reload nginx
if [[ "$os_family" == rhel ]] && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="$backend_port/tcp"
  firewall-cmd --reload
fi
ready=0
for ((attempt=0;attempt<60;attempt++)); do
  if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/v1/health > "$tmp/health.json"; then ready=1; break; fi
  sleep 2
done
((ready == 1)) || { echo 'Healthcheck failed. Inspect systemctl status cloudportal-api cloudportal-dispatcher cloudportal-worker@1. Bootstrap token has not been generated.' >&2; exit 1; }
curl -fsS --noproxy '*' --connect-timeout 5 --max-time 15 \
  --cacert "$config/tls/server.crt" --resolve "$backend_host:$backend_port:127.0.0.1" \
  "https://$backend_host:$backend_port/api/v1/health" > "$tmp/tls-health.json" || {
  echo 'HTTPS healthcheck failed. Check the certificate hostname/expiry and Nginx. Bootstrap token has not been generated.' >&2; exit 1;
}
# Secrets are created only after services are healthy, printed only here and never written to logs/files.
run_backend "$release/.venv/bin/python" -m app.bootstrap --url "https://$backend_host:$backend_port"
