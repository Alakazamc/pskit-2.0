#!/usr/bin/env bash
# Prepare or apply a pinned gVisor runsc runtime without restarting Docker.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: install_gvisor_runtime.sh --package PATH --sha512 HEX --version VERSION [--apply]

PACKAGE is the official gvisor.tar.bz2 release bundle. Without --apply the
script validates the bundle and prints the Docker daemon.json diff. With
--apply it requires root, installs a versioned bundle, backs up daemon.json,
and writes the merged configuration. It never reloads or restarts Docker.
EOF
}

package=
expected_sha512=
version=
apply=false
while (($#)); do
  case "$1" in
    --package) package=${2:-}; shift 2 ;;
    --sha512) expected_sha512=${2:-}; shift 2 ;;
    --version) version=${2:-}; shift 2 ;;
    --apply) apply=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

[[ -n "$package" && -f "$package" && ! -L "$package" ]] || {
  echo "A regular local --package file is required" >&2; exit 2;
}
[[ "$expected_sha512" =~ ^[a-f0-9]{128}$ ]] || {
  echo "--sha512 must be 128 lower-case hexadecimal characters" >&2; exit 2;
}
[[ "$version" =~ ^release-[0-9]{8}\.[0-9]+$ ]] || {
  echo "--version must look like release-YYYYMMDD.RC" >&2; exit 2;
}
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "This installer only supports Linux x86_64" >&2; exit 2;
}

actual_sha512=$(sha512sum "$package" | awk '{print $1}')
[[ "$actual_sha512" == "$expected_sha512" ]] || {
  echo "gVisor bundle checksum mismatch" >&2; exit 1;
}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
tar -xjf "$package" -C "$scratch"
probe="$scratch/runsc"
[[ -x "$probe" && -x "$scratch/containerd-shim-runsc-v1" && -d "$scratch/gvisor-bin" ]] || {
  echo "gVisor release bundle is incomplete" >&2; exit 1;
}
reported=$("$probe" --version 2>&1 || true)
[[ "$reported" == *"$version"* ]] || {
  echo "runsc did not report the requested version" >&2; exit 1;
}

daemon=/etc/docker/daemon.json
target_dir="/usr/local/lib/pskit-gvisor/$version"
target="$target_dir/runsc"
candidate="$scratch/daemon.json"
python3 - "$daemon" "$candidate" "$target" <<'PY'
import json
import pathlib
import sys

source, destination, runtime = map(pathlib.Path, sys.argv[1:])
if source.exists():
    if source.is_symlink() or not source.is_file():
        raise SystemExit("Docker daemon configuration is not a regular file")
    data = json.loads(source.read_text())
else:
    data = {}
if not isinstance(data, dict):
    raise SystemExit("Docker daemon configuration must be a JSON object")
runtimes = data.setdefault("runtimes", {})
if not isinstance(runtimes, dict):
    raise SystemExit("Docker runtimes configuration must be an object")
existing = runtimes.get("runsc")
desired = {"path": str(runtime), "runtimeArgs": []}
if existing not in (None, desired):
    raise SystemExit("A different runsc runtime is already registered")
runtimes["runsc"] = desired
destination.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY

echo "Validated gVisor $version ($actual_sha512)"
if [[ -f "$daemon" ]]; then
  diff -u "$daemon" "$candidate" || true
else
  diff -u /dev/null "$candidate" || true
fi

if [[ "$apply" != true ]]; then
  echo "Preview only. Re-run as root with --apply after reviewing the diff."
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo "--apply requires root" >&2; exit 2; }

install -d -o root -g root -m 0755 "$target_dir"
cp -a "$scratch/." "$target_dir/"
chown -R root:root "$target_dir"
find "$target_dir" -type d -exec chmod 0755 {} +
find "$target_dir" -type f -exec chmod 0755 {} +
mkdir -p /etc/docker
if [[ -f "$daemon" ]]; then
  backup="$daemon.pre-runsc-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -a "$daemon" "$backup"
  echo "Docker configuration backup: $backup"
fi
install -o root -g root -m 0644 "$candidate" "$daemon"
echo "runsc was installed and registered. Docker was not reloaded."
echo "Review running containers, then reload Docker using the host's approved procedure."
echo "Afterward run check_gvisor_runtime.sh with a pinned probe image digest."
