#!/usr/bin/env bash
# Install the host-local PSKit quota volume driver. Does not restart Docker.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: install_quota_volume_driver.sh --root ABSOLUTE_PATH [--apply]

Without --apply this checks host prerequisites and prints the intended paths.
With --apply it installs and starts the root-only Unix-socket volume plugin.
Existing volume images and mount points are preserved.
EOF
}

root=
apply=false
while (($#)); do
  case "$1" in
    --root) root=${2:-}; shift 2 ;;
    --apply) apply=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

[[ "$root" == /* && "$root" != / && "$root" != /var && "$root" != /data ]] || {
  echo "--root must be a dedicated absolute directory" >&2; exit 2;
}
[[ ! -L "$root" ]] || { echo "--root cannot be a symlink" >&2; exit 2; }
for command in python3 mkfs.ext4 mount umount systemctl; do
  command -v "$command" >/dev/null || { echo "$command is unavailable" >&2; exit 1; }
done
grep -q '^nodev[[:space:]]*/sys/fs/ext4' /proc/filesystems 2>/dev/null || \
  grep -q 'ext4' /proc/filesystems || { echo "ext4 is unavailable" >&2; exit 1; }
[[ -e /dev/loop-control ]] || { echo "/dev/loop-control is unavailable" >&2; exit 1; }

source_file=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../quota-volume" && pwd)/pskit_quota_volume.py
[[ -f "$source_file" && ! -L "$source_file" ]] || {
  echo "quota volume plugin source is unavailable" >&2; exit 1;
}
socket=/run/docker/plugins/pskit-quota.sock
target=/usr/local/libexec/pskit-quota-volume
unit=/etc/systemd/system/pskit-quota-volume.service

echo "Validated quota volume driver prerequisites"
echo "Plugin source: $source_file"
echo "Persistent root: $root"
echo "Docker socket: $socket"
if [[ "$apply" != true ]]; then
  echo "Preview only. Re-run as root with --apply after reviewing these paths."
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo "--apply requires root" >&2; exit 2; }

install -d -o root -g root -m 0700 "$root" "$root/images" "$root/mounts" "$root/state"
install -d -o root -g root -m 0755 /usr/local/libexec /run/docker/plugins
install -o root -g root -m 0555 "$source_file" "$target"

temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT
cat >"$temporary" <<EOF
[Unit]
Description=PSKit bounded ext4 Docker volume driver
After=local-fs.target
Before=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 $target --root $root --socket $socket
Restart=on-failure
RestartSec=2
User=root
Group=root
UMask=0177
NoNewPrivileges=true
CapabilityBoundingSet=CAP_SYS_ADMIN
AmbientCapabilities=CAP_SYS_ADMIN
RestrictAddressFamilies=AF_UNIX
PrivateMounts=false

[Install]
WantedBy=multi-user.target
EOF
if [[ -f "$unit" ]]; then
  backup="$unit.pre-pskit-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -a "$unit" "$backup"
  echo "Service unit backup: $backup"
fi
install -o root -g root -m 0644 "$temporary" "$unit"
systemctl daemon-reload
systemctl enable --now pskit-quota-volume.service
for _ in $(seq 1 30); do
  [[ -S "$socket" ]] && break
  sleep 0.1
done
[[ -S "$socket" ]] || { echo "quota volume socket did not start" >&2; exit 1; }
systemctl is-active --quiet pskit-quota-volume.service
echo "PSKit quota volume driver is active. Docker was not restarted."
