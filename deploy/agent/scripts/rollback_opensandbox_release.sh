#!/usr/bin/env bash
# Disable workspace admission, drain attempts, restore disabled-provider backend, preserve data.
set -Eeuo pipefail

agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cloud_env=${STACK_CLOUD_ENV_FILE:-$agent_dir/cloud.env}
drain_seconds=120
while (($#)); do
  case "$1" in
    --cloud-env) cloud_env=${2:-}; shift 2 ;;
    --drain-seconds) drain_seconds=${2:-}; shift 2 ;;
    *) echo "Unexpected argument" >&2; exit 2 ;;
  esac
done
[[ "$drain_seconds" =~ ^[0-9]+$ ]] || { echo "Invalid drain duration" >&2; exit 2; }

env_value() {
  python3 - "$1" "$2" <<'PY'
import pathlib
import sys

values = dict(
    line.split("=", 1)
    for line in pathlib.Path(sys.argv[1]).read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
if not values.get(sys.argv[2]):
    raise SystemExit("required configuration key is missing")
print(values[sys.argv[2]])
PY
}
resolve_env_path() {
  python3 - "$1" "$2" <<'PY'
import pathlib
import sys

env_file = pathlib.Path(sys.argv[1]).resolve()
value = pathlib.Path(sys.argv[2])
print(value if value.is_absolute() else (env_file.parent / value).resolve())
PY
}

[[ -f "$cloud_env" && ! -L "$cloud_env" ]] || {
  echo "Production cloud environment is unavailable" >&2; exit 2;
}
rollout_dir=$(resolve_env_path "$cloud_env" "$(env_value "$cloud_env" OPENSANDBOX_ROLLOUT_DIR)")
capability_hash=$(python3 - "$rollout_dir/policy.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(json.loads(path.read_text()).get("capability_hash", ""))
PY
)
python3 "$agent_dir/scripts/set_workspace_rollout.py" "$rollout_dir" \
  --no-enabled --no-commands-enabled --capability-hash "$capability_hash"

attempt_counts() {
  docker exec supabase-db psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d postgres -c \
    "SELECT count(*) FILTER (WHERE status IN ('queued','running','cancelling')), count(*) FILTER (WHERE status='unknown') FROM pskit.workspace_attempts;"
}
deadline=$((SECONDS + drain_seconds))
while true; do
  counts=$(attempt_counts)
  active=${counts%%|*}
  unknown=${counts##*|}
  [[ "$unknown" == 0 ]] || {
    echo "Rollback refused because a workspace attempt is unknown" >&2; exit 2;
  }
  [[ "$active" == 0 ]] && break
  (( SECONDS < deadline )) || {
    echo "Rollback drain timed out; provider and volumes were left running" >&2; exit 2;
  }
  sleep 2
done

with_opensandbox=(docker compose --env-file "$cloud_env"
  -f "$agent_dir/compose.yaml"
  -f "$agent_dir/compose.cloud.yaml"
  -f "$agent_dir/compose.postgres.yaml"
  -f "$agent_dir/compose.opensandbox.yaml"
  -f "$agent_dir/compose.opensandbox.production.yaml"
  -p pskit-agent-cloud)
without_opensandbox=(docker compose --env-file "$cloud_env"
  -f "$agent_dir/compose.yaml"
  -f "$agent_dir/compose.cloud.yaml"
  -f "$agent_dir/compose.postgres.yaml"
  -p pskit-agent-cloud)

"${without_opensandbox[@]}" up -d --no-deps --wait backend
curl --noproxy '*' --fail --silent http://127.0.0.1:18088/health/ready >/dev/null
"${with_opensandbox[@]}" stop opensandbox-server
echo "Workspace access is disabled; OpenSandbox state and user volumes were retained"
