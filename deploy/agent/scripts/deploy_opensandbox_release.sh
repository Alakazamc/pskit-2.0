#!/usr/bin/env bash
# Cut a qualified Staging OpenSandbox release into production for all users.
set -Eeuo pipefail

agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repo_dir=$(cd -- "$agent_dir/../.." && pwd)

manifest=
cloud_env=${STACK_CLOUD_ENV_FILE:-$agent_dir/cloud.env}
backend_env=${STACK_BACKEND_ENV_FILE:-$agent_dir/cloud.backend.env}
admin_env=${STACK_ADMIN_ENV_FILE:-$agent_dir/.env.stack-admin}
supabase_network=${STACK_SUPABASE_NETWORK:-pskit-agent-supabase_default}

while (($#)); do
  case "$1" in
    --manifest) manifest=${2:-}; shift 2 ;;
    --cloud-env) cloud_env=${2:-}; shift 2 ;;
    --backend-env) backend_env=${2:-}; shift 2 ;;
    --admin-env) admin_env=${2:-}; shift 2 ;;
    *) echo "Unexpected argument" >&2; exit 2 ;;
  esac
done
[[ -n "$manifest" ]] || {
  echo "Usage: deploy_opensandbox_release.sh --manifest PATH" >&2
  exit 2
}

private_file() {
  [[ -f "$1" && ! -L "$1" && ( $(stat -c %a "$1") =~ ^[0-7]00$ ) ]] || {
    echo "Missing or unsafe private file" >&2; exit 2;
  }
}
env_value() {
  python3 - "$1" "$2" <<'PY'
import pathlib
import sys

path, key = pathlib.Path(sys.argv[1]), sys.argv[2]
values = {}
for line in path.read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        if name in values:
            raise SystemExit("duplicate configuration key")
        values[name] = value
if not values.get(key):
    raise SystemExit("required configuration key is missing")
print(values[key])
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
manifest_value() {
  python3 - "$manifest" "$1" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text())
for part in sys.argv[2].split("."):
    value = value[part]
if not isinstance(value, (str, int)):
    raise SystemExit("manifest value is invalid")
print(value)
PY
}
registered_runsc_path() {
  docker info --format '{{json .Runtimes}}' | python3 -c \
    'import json,sys; value=json.load(sys.stdin).get("runsc") or {}; print(value.get("path") or value.get("Path") or "")'
}

private_file "$manifest"
private_file "$cloud_env"
private_file "$backend_env"
private_file "$admin_env"
[[ $(manifest_value status) == qualified ]] || {
  echo "Release manifest is not qualified by Staging" >&2; exit 2;
}
backend_image=$(manifest_value backend.reference)
server_image=$(manifest_value opensandbox.server.reference)
sandbox_image=$(manifest_value opensandbox.sandbox.reference)
capability_hash=$(manifest_value opensandbox.capability_hash)
runsc_version=$(manifest_value opensandbox.runsc_version)
staging_namespace=$(manifest_value opensandbox.namespace)
staging_network=$(manifest_value opensandbox.runtime_network)
staging_volume=$(manifest_value opensandbox.state_volume)
staging_key_hash=$(manifest_value opensandbox.api_key_sha256)

[[ $(env_value "$cloud_env" AGENT_BACKEND_IMAGE) == "$backend_image" ]] || {
  echo "Production backend differs from the qualified release" >&2; exit 2;
}
[[ $(env_value "$cloud_env" OPENSANDBOX_SERVER_IMAGE) == "$server_image" ]] || {
  echo "Production OpenSandbox Server differs from the qualified release" >&2; exit 2;
}
[[ $(env_value "$cloud_env" OPENSANDBOX_SANDBOX_IMAGE) == "$sandbox_image" ]] || {
  echo "Production sandbox image differs from the qualified release" >&2; exit 2;
}
production_namespace=$(env_value "$cloud_env" OPENSANDBOX_NAMESPACE)
production_network=$(env_value "$cloud_env" OPENSANDBOX_RUNTIME_NETWORK)
production_volume=$(env_value "$cloud_env" OPENSANDBOX_STATE_VOLUME)
rollout_dir=$(resolve_env_path "$cloud_env" "$(env_value "$cloud_env" OPENSANDBOX_ROLLOUT_DIR)")
production_key=$(env_value "$cloud_env" OPENSANDBOX_API_KEY)
production_key_hash=$(printf '%s' "$production_key" | sha256sum | awk '{print $1}')
[[ "$production_namespace" != "$staging_namespace" &&
   "$production_network" != "$staging_network" &&
   "$production_volume" != "$staging_volume" &&
   "$production_key_hash" != "$staging_key_hash" ]] || {
  echo "Production and Staging OpenSandbox identities are not isolated" >&2; exit 2;
}
runsc_path=$(registered_runsc_path)
[[ -x "$runsc_path" && $("$runsc_path" --version 2>&1) == *"$runsc_version"* ]] || {
  echo "Production runsc version differs from the qualified release" >&2; exit 2;
}
docker image inspect "$backend_image" "$server_image" "$sandbox_image" >/dev/null

mkdir -p "$rollout_dir"
chmod 0755 "$rollout_dir"
python3 "$agent_dir/scripts/set_workspace_rollout.py" "$rollout_dir" \
  --no-enabled --no-commands-enabled --capability-hash "$capability_hash"

compose=(docker compose --env-file "$cloud_env"
  -f "$agent_dir/compose.yaml"
  -f "$agent_dir/compose.cloud.yaml"
  -f "$agent_dir/compose.postgres.yaml"
  -f "$agent_dir/compose.opensandbox.yaml"
  -f "$agent_dir/compose.opensandbox.production.yaml"
  -p pskit-agent-cloud)
"${compose[@]}" config --quiet

config_file=$(resolve_env_path "$cloud_env" "$(env_value "$cloud_env" OPENSANDBOX_CONFIG_FILE)")
python3 "$agent_dir/scripts/opensandbox_preflight.py" \
  --env-file "$cloud_env" --config "$config_file" \
  --compose-file "$agent_dir/compose.yaml" \
  --compose-file "$agent_dir/compose.cloud.yaml" \
  --compose-file "$agent_dir/compose.postgres.yaml" \
  --compose-file "$agent_dir/compose.opensandbox.yaml" \
  --compose-file "$agent_dir/compose.opensandbox.production.yaml" >/dev/null

docker run --rm --network "$supabase_network" --env-file "$admin_env" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --entrypoint python "$backend_image" -c \
  'import os; from app.db.postgres_migrations import migrate_postgres; migrate_postgres(os.environ["SHARED_POSTGRES_ADMIN_DSN"])'

attempt_counts() {
  docker exec supabase-db psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d postgres -c \
    "SELECT count(*) FILTER (WHERE status IN ('queued','running','cancelling')), count(*) FILTER (WHERE status='unknown') FROM pskit.workspace_attempts;"
}
counts=$(attempt_counts)
[[ "$counts" == "0|0" ]] || {
  echo "Production has active or unknown workspace attempts" >&2; exit 2;
}

rollback_on_error() {
  python3 "$agent_dir/scripts/set_workspace_rollout.py" "$rollout_dir" \
    --no-enabled --no-commands-enabled --capability-hash "$capability_hash" >/dev/null || true
  "${compose[@]}" stop opensandbox-server >/dev/null 2>&1 || true
}
trap rollback_on_error ERR

"${compose[@]}" up -d --wait opensandbox-server
report=$(mktemp)
trap 'rm -f "$report"; rollback_on_error' ERR
python3 "$agent_dir/scripts/opensandbox_preflight.py" \
  --env-file "$cloud_env" --config "$config_file" \
  --compose-file "$agent_dir/compose.yaml" \
  --compose-file "$agent_dir/compose.cloud.yaml" \
  --compose-file "$agent_dir/compose.postgres.yaml" \
  --compose-file "$agent_dir/compose.opensandbox.yaml" \
  --compose-file "$agent_dir/compose.opensandbox.production.yaml" --live >"$report"
actual_capability_hash=$(python3 - "$report" <<'PY'
import hashlib
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text())["live_probe"]
contract = {
    "cancellation": report["cancellation"],
    "command_events": report["command_events"],
    "command_execution": True,
    "file_access": True,
    "persistent_volume": report["persistent_volume"],
    "provider": "opensandbox",
    "runtime": report["runtime"],
    "session_mount_namespace": report["session_mount_namespace"],
}
print(hashlib.sha256(json.dumps(
    contract, sort_keys=True, separators=(",", ":")
).encode()).hexdigest())
PY
)
[[ "$actual_capability_hash" == "$capability_hash" ]] || {
  echo "Production capability report differs from Staging" >&2; exit 2;
}
rm -f "$report"

python3 "$agent_dir/scripts/set_workspace_rollout.py" "$rollout_dir" \
  --enabled --commands-enabled --user '*' \
  --capability-hash "$capability_hash"
"${compose[@]}" up -d --no-deps --wait backend
curl --noproxy '*' --fail --silent http://127.0.0.1:18088/health/ready >/dev/null

python3 - "$manifest" "$rollout_dir" <<'PY'
import datetime
import hashlib
import json
import os
import pathlib
import sys
import tempfile

manifest, rollout = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
record = {
    "deployed_at": datetime.datetime.now(datetime.UTC).isoformat(),
    "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    "rollout": "basic-tools",
    "allowlisted_users": ["*"],
}
fd, temporary = tempfile.mkstemp(prefix=".production-active.", dir=rollout)
with os.fdopen(fd, "w") as stream:
    json.dump(record, stream, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(temporary, 0o600)
os.replace(temporary, rollout / "production-active.json")
PY
trap - ERR
echo "OpenSandbox production rollout enabled basic tools for all users"
