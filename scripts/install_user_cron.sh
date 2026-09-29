#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runner="$root_dir/scripts/run_scheduled_job.sh"
marker_start="# BEGIN PSKIT MANAGED SCHEDULE"
marker_end="# END PSKIT MANAGED SCHEDULE"

if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab is unavailable; ask an administrator to enable systemd linger instead" >&2
  exit 2
fi
if [[ ! -x "$runner" ]]; then
  echo "Scheduled-job runner is not executable: $runner" >&2
  exit 2
fi
if [[ "$root_dir" == *$'\n'* ]]; then
  echo "Runtime path must not contain a newline" >&2
  exit 2
fi

tmp="$(mktemp)"
trap 'rm -f -- "$tmp"' EXIT
crontab -l 2>/dev/null | awk -v start="$marker_start" -v end="$marker_end" '
  $0 == start { managed = 1; next }
  $0 == end { managed = 0; next }
  !managed { print }
' > "$tmp" || true

{
  cat "$tmp"
  printf '%s\n' "$marker_start"
  printf 'CRON_TZ=Asia/Shanghai\n'
  printf '*/5 * * * * %q health\n' "$runner"
  printf '20 3 * * * %q backup\n' "$runner"
  printf '%s\n' "$marker_end"
} | crontab -

echo "Installed PSKit health (every 5 minutes) and backup (daily 03:20 Asia/Shanghai) jobs."
crontab -l | awk -v start="$marker_start" -v end="$marker_end" '
  $0 == start { managed = 1 }
  managed { print }
  $0 == end { exit }
'
