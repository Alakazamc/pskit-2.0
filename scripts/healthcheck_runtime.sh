#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
base_url="${PSKIT_HEALTH_URL:-http://127.0.0.1:10716}"
backup_root="${PSKIT_BACKUP_ROOT:-$root_dir/backups}"
disk_path="${PSKIT_DISK_PATH:-/}"
max_disk_percent="${PSKIT_MAX_DISK_PERCENT:-92}"
max_backup_age_hours="${PSKIT_MAX_BACKUP_AGE_HOURS:-26}"

ready="$(curl --fail --silent --show-error --max-time 15 "$base_url/api/ready")"
python3 -c 'import json,sys; assert json.load(sys.stdin)["ok"] is True' <<< "$ready"

used_percent="$(df -P "$disk_path" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
if (( used_percent >= max_disk_percent )); then
  echo "Disk threshold exceeded: ${used_percent}% >= ${max_disk_percent}%" >&2
  exit 4
fi

latest="$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
  -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)"
if [[ -z "$latest" || ! -f "$latest/SHA256SUMS" ]]; then
  echo "No verified runtime backup found under $backup_root" >&2
  exit 5
fi
age_hours="$(( ($(date +%s) - $(stat -c %Y "$latest/SHA256SUMS")) / 3600 ))"
if (( age_hours > max_backup_age_hours )); then
  echo "Latest backup is stale: ${age_hours}h > ${max_backup_age_hours}h" >&2
  exit 6
fi

printf '{"ready":true,"disk_percent":%s,"backup_age_hours":%s,"backup":"%s"}\n' \
  "$used_percent" "$age_hours" "$latest"
