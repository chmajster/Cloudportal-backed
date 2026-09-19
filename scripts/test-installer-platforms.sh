#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixture=$(mktemp)
trap 'rm -f "$fixture"' EXIT

check_supported() {
  local id=$1 version=$2 family=$3 store=$4 python=$5 output
  printf 'ID=%s\nVERSION_ID=%s\nNAME="Test %s"\n' "$id" "$version" "$id" > "$fixture"
  output=$(CLOUDPORTAL_OS_RELEASE_FILE="$fixture" "$repo_root/install.sh" --check-platform)
  grep -Fq "[ OK ] Obsługiwany system: Test $id $version" <<< "$output"
  grep -Fq "Rodzina: $family" <<< "$output"
  grep -Fq "KV: $store" <<< "$output"
  grep -Fq "Python: $python" <<< "$output"
}

check_supported ubuntu 24.04 debian redis-server python3
check_supported ubuntu 26.04 debian redis-server python3
check_supported debian 12 debian redis-server python3
check_supported debian 13 debian redis-server python3
check_supported rhel 9.6 rhel redis python3.12
check_supported rhel 10.1 rhel valkey python3

help_long=$("$repo_root/install.sh" --gui --help)
help_short=$("$repo_root/install.sh" -gui --help)
grep -Fq -- '--gui, -gui' <<< "$help_long"
grep -Fq -- '--gui, -gui' <<< "$help_short"
grep -Fq -- '--status' <<< "$help_long"
grep -Fq -- '--uninstall' <<< "$help_long"
grep -Fq 'GUI mode requires an interactive TTY.' "$repo_root/install.sh"
grep -Fq 'dialog --stdout' "$repo_root/install.sh"
grep -Fq '</dev/tty' "$repo_root/install.sh"

# GitHub installation tokens may contain dots and hyphens. Keep quotes and
# control characters forbidden because the value is written to curl config.
grep -Fq '^[A-Za-z0-9._-]{20,512}$' "$repo_root/install.sh"

printf 'ID=ubuntu\nVERSION_ID=25.10\nNAME="Unsupported Ubuntu"\n' > "$fixture"
if CLOUDPORTAL_OS_RELEASE_FILE="$fixture" "$repo_root/install.sh" --check-platform >/dev/null 2>&1; then
  echo 'Unsupported platform was accepted.' >&2
  exit 1
fi

echo 'Installer platform detection: passed.'
