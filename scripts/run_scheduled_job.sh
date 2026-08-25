#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
job="${1:-}"
state_dir="${PSKIT_SCHEDULE_STATE_DIR:-$root_dir/.runtime/schedule}"
log_dir="${PSKIT_SCHEDULE_LOG_DIR:-$root_dir/logs}"
max_log_bytes="${PSKIT_SCHEDULE_MAX_LOG_BYTES:-5242880}"

mkdir -p "$state_dir" "$log_dir"
chmod 700 "$state_dir"

case "$job" in
  health|backup) ;;
  *)
    echo "Usage: $0 {health|backup}" >&2
    exit 2
    ;;
esac

log_file="$log_dir/${job}.log"
if [[ -f "$log_file" ]] && (( $(stat -c %s "$log_file") > max_log_bytes )); then
  tail -n 2000 "$log_file" > "$log_file.tmp"
  mv -- "$log_file.tmp" "$log_file"
fi

exec 9>"$state_dir/${job}.lock"
if ! flock -n 9; then
  printf '%s job=%s status=skipped reason=already-running\n' \
    "$(date --iso-8601=seconds)" "$job" >> "$log_file"
  exit 0
fi

run_job() {
  case "$job" in
    health)
      PSKIT_HEALTH_URL="${PSKIT_HEALTH_URL:-https://pskit.bioailab.net}" \
      PSKIT_BACKUP_ROOT="${PSKIT_BACKUP_ROOT:-/data1/enine/pskit-backups}" \
      PSKIT_DISK_PATH="${PSKIT_DISK_PATH:-/}" \
        "$root_dir/scripts/healthcheck_runtime.sh"
      ;;
    backup)
      COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pskit2-v020-smoke}" \
      PSKIT_COMPOSE_FILES="${PSKIT_COMPOSE_FILES:-compose.yaml:compose.science.server.yaml:compose.gateway.server.yaml:compose.mcp.a6000.yaml}" \
      PSKIT_EXTRA_CONFIG_FILES="${PSKIT_EXTRA_CONFIG_FILES:-.env.mcp}" \
      PSKIT_BACKUP_ROOT="${PSKIT_BACKUP_ROOT:-/data1/enine/pskit-backups}" \
        "$root_dir/scripts/backup_runtime.sh" "$root_dir/.env.docker"
      ;;
  esac
}

started="$(date --iso-8601=seconds)"
if run_job >> "$log_file" 2>&1; then
  printf '%s job=%s status=success started=%s\n' \
    "$(date --iso-8601=seconds)" "$job" "$started" >> "$log_file"
else
  status=$?
  printf '%s job=%s status=failed exit_code=%s started=%s\n' \
    "$(date --iso-8601=seconds)" "$job" "$status" "$started" >> "$log_file"
  exit "$status"
fi
