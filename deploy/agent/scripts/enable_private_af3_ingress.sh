#!/usr/bin/env bash
# Run as root after the cloud AF3 callback proxy is healthy.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on Aliyun" >&2
    exit 1
fi

conf_dir="${AGENT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
disabled_conf="$conf_dir/agent-af3-private.conf.disabled-20261002"
active_conf="$conf_dir/agent-af3-private.conf"
test -f "$disabled_conf"
if [ -e "$active_conf" ]; then
    echo "AF3 private ingress is already active: $active_conf" >&2
    exit 1
fi

status="$(curl --noproxy '*' --connect-timeout 5 --max-time 10 -sS \
    -o /dev/null -w '%{http_code}' \
    'http://127.0.0.1:18185/internal/compute/af3/jobs/owned?worker_id=a6000-af3-cloud-1')"
if [ "$status" != 404 ]; then
    echo "AF3 callback proxy preflight failed (HTTP $status)" >&2
    exit 1
fi

restore_previous() {
    mv -- "$active_conf" "$disabled_conf"
    nginx -t && systemctl reload nginx
}

mv -- "$disabled_conf" "$active_conf"
if ! nginx -t || ! systemctl reload nginx; then
    restore_previous
    echo "AF3 private ingress failed; disabled configuration restored" >&2
    exit 1
fi

# The cloud host itself is not the allowed A6000 source.
status="$(curl --noproxy '*' --connect-timeout 5 --max-time 10 -sS \
    -o /dev/null -w '%{http_code}' \
    'http://10.9.8.1:18184/internal/compute/af3/jobs/owned?worker_id=a6000-af3-cloud-1')" || {
    restore_previous
    echo "AF3 private ingress probe failed; disabled configuration restored" >&2
    exit 1
}
if [ "$status" != 403 ]; then
    restore_previous
    echo "AF3 private ingress source restriction failed; configuration restored" >&2
    exit 1
fi

echo "AF3 private ingress enabled for 10.9.8.2 only"
