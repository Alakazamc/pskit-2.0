#!/bin/sh
set -eu

umask 077
test "$(id -u)" = 0
mountpoint -q /workspace
mountpoint -q /tmp/pskit-workspace-root
probe="/workspace/.pskit-volume-alias-probe.$$"
trap 'rm -f "$probe"' EXIT HUP INT TERM
: >"$probe"
test -f "/tmp/pskit-workspace-root/${probe##*/}"
rm -f "$probe"
trap - EXIT HUP INT TERM
exec /bin/sleep infinity
