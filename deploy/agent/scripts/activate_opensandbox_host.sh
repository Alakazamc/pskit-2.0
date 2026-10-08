#!/usr/bin/env bash
# Explicit root-only activation of fixed gVisor and the PSKit quota driver.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: activate_opensandbox_host.sh \
  --gvisor-package PATH --gvisor-sha512 HEX \
  --gvisor-version release-YYYYMMDD.RC \
  --volume-root PATH --probe-image IMAGE@sha256:DIGEST

This is the explicit host cutover step. It installs the reviewed artifacts,
reloads (never restarts) Docker, runs a no-network runsc probe, and verifies
that every previously running container kept the same container ID.
EOF
}

package=
sha512=
version=
volume_root=
probe_image=
while (($#)); do
  case "$1" in
    --gvisor-package) package=${2:-}; shift 2 ;;
    --gvisor-sha512) sha512=${2:-}; shift 2 ;;
    --gvisor-version) version=${2:-}; shift 2 ;;
    --volume-root) volume_root=${2:-}; shift 2 ;;
    --probe-image) probe_image=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "Host activation requires root" >&2; exit 2; }

agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
before=$(mktemp)
after=$(mktemp)
trap 'rm -f "$before" "$after"' EXIT
docker ps --format '{{.Names}} {{.ID}}' | sort >"$before"

bash "$agent_dir/scripts/install_gvisor_runtime.sh" \
  --package "$package" --sha512 "$sha512" --version "$version" --apply
bash "$agent_dir/scripts/install_quota_volume_driver.sh" \
  --root "$volume_root" --apply

systemctl reload docker
for _ in $(seq 1 60); do
  docker info >/dev/null 2>&1 && break
  sleep 0.5
done
docker info >/dev/null
bash "$agent_dir/scripts/check_gvisor_runtime.sh" "$probe_image" "$version"

docker ps --format '{{.Names}} {{.ID}}' | sort >"$after"
if ! cmp -s "$before" "$after"; then
  echo "Docker reload changed the running container set:" >&2
  diff -u "$before" "$after" >&2 || true
  exit 1
fi
echo "OpenSandbox host prerequisites are active; existing containers were preserved"
