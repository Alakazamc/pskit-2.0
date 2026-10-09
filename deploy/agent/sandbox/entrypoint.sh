#!/bin/sh
set -eu

umask 077
test "$(id -u)" = 0
install -d -m 0700 /tmp/pskit-workspace-root
mount --bind /workspace /tmp/pskit-workspace-root
mountpoint -q /tmp/pskit-workspace-root
exec /bin/sleep infinity
