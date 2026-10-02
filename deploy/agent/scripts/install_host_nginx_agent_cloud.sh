#!/usr/bin/env bash
# Run as root only after the returned Aliyun backend passes private checks.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on Aliyun" >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_conf="$script_dir/../host-nginx-agent-aliyun.conf"
conf_dir="${AGENT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
target_conf="$conf_dir/agent.bioailab.net.conf"
backup_conf="$conf_dir/agent.bioailab.net.conf.pre-aliyun-20261003"

test -f "$source_conf"
test -f "$target_conf"
if [ -e "$backup_conf" ]; then
    echo "Review the existing rollback copy before another cutover: $backup_conf" >&2
    exit 1
fi

status="$(curl --noproxy '*' --connect-timeout 5 --max-time 10 -sS \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:18088/api/v1/usage)"
if [ "$status" != 401 ]; then
    echo "Aliyun backend preflight failed (HTTP $status)" >&2
    exit 1
fi

cp -a -- "$target_conf" "$backup_conf"
install -m 0644 "$source_conf" "$target_conf"

restore_previous() {
    cp -a -- "$backup_conf" "$target_conf"
    nginx -t && systemctl reload nginx
}

if ! nginx -t || ! systemctl reload nginx; then
    restore_previous
    echo "Nginx cutover failed; previous public configuration restored" >&2
    exit 1
fi

if ! curl --noproxy '*' --resolve agent.bioailab.net:443:127.0.0.1 \
    --connect-timeout 5 --max-time 10 -fsS -o /dev/null \
    https://agent.bioailab.net/login; then
    restore_previous
    echo "Public login probe failed; previous configuration restored" >&2
    exit 1
fi

status="$(curl --noproxy '*' --resolve agent.bioailab.net:443:127.0.0.1 \
    --connect-timeout 5 --max-time 10 -sS -o /dev/null -w '%{http_code}' \
    https://agent.bioailab.net/api/v1/usage)" || {
    restore_previous
    echo "Public API probe failed; previous configuration restored" >&2
    exit 1
}
if [ "$status" != 401 ]; then
    restore_previous
    echo "Public API returned HTTP $status; previous configuration restored" >&2
    exit 1
fi

echo "Public Agent API now routes to the Aliyun backend"
