#!/usr/bin/env bash
# Read-only host summary plus one disposable no-network runsc probe.
set -Eeuo pipefail

probe_image=${1:-}
expected_version=${2:-}
[[ "$probe_image" =~ ^[^[:space:]@]+@sha256:[a-f0-9]{64}$ ]] || {
  echo "Usage: check_gvisor_runtime.sh IMAGE@sha256:<64-hex> EXPECTED_RUNSC_VERSION" >&2
  exit 2
}
[[ -n "$expected_version" ]] || { echo "Expected runsc version is required" >&2; exit 2; }

command -v docker >/dev/null || { echo "docker is unavailable" >&2; exit 1; }
runtime_path=$(python3 - <<'PY'
import json
import subprocess

raw = subprocess.check_output(["docker", "info", "--format", "{{json .Runtimes}}"], text=True)
runtime = json.loads(raw).get("runsc")
if not runtime:
    raise SystemExit("runsc is not registered in Docker")
print(runtime.get("path") or runtime.get("Path") or "runsc")
PY
)
reported=$("$runtime_path" --version 2>&1)
[[ "$reported" == *"$expected_version"* ]] || {
  echo "Registered runsc version does not match $expected_version" >&2; exit 1;
}

echo "Registered runtime: runsc ($expected_version)"
docker run --rm --runtime=runsc --network=none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  "$probe_image" /bin/sh -c \
  'test -r /proc/self/status; test ! -w /; printf "runsc-probe-ok\n"'

echo "Existing containers (name image status runtime):"
docker ps --format '{{.Names}}' | while IFS= read -r container; do
  docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.Status}} {{.HostConfig.Runtime}}' \
    "$container" | sed 's#^/##'
done
