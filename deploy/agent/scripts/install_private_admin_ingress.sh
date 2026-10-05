#!/usr/bin/env bash
# Restrict the production admin UI and API to Aliyun's WireGuard network.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on Aliyun" >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_conf="$script_dir/../host-nginx-agent-aliyun.conf"
conf_dir="${AGENT_NGINX_CONF_DIR:-/etc/nginx/conf.d}"
target_conf="$conf_dir/agent.bioailab.net.conf"

test -f "$source_conf"
test -f "$target_conf"
if cmp -s -- "$source_conf" "$target_conf"; then
    echo "Private admin ingress is already installed"
    exit 0
fi

backup_conf="$(mktemp "$conf_dir/agent.bioailab.net.conf.pre-admin-private.XXXXXXXX")"
install -m 0600 -- "$target_conf" "$backup_conf"
install -m 0644 -- "$source_conf" "$target_conf"

restore_previous() {
    install -m 0644 -- "$backup_conf" "$target_conf"
    nginx -t && systemctl reload nginx
}

if ! nginx -t || ! systemctl reload nginx; then
    restore_previous
    echo "Nginx reload failed; previous configuration restored" >&2
    exit 1
fi

probe_status() {
    local ip="$1" path="$2"
    curl --noproxy '*' --resolve "agent.bioailab.net:443:$ip" \
        --connect-timeout 2 --max-time 5 -sS -o /dev/null -w '%{http_code}' \
        "https://agent.bioailab.net$path" || true
}

attempts="${AGENT_ADMIN_PROBE_ATTEMPTS:-20}"
for ((attempt=1; attempt<=attempts; attempt++)); do
    public_page="$(probe_status 127.0.0.1 /admin/models)"
    public_page_upper="$(probe_status 127.0.0.1 /ADMIN/models)"
    public_api="$(probe_status 127.0.0.1 /api/v1/admin/me)"
    private_page="$(probe_status 10.9.8.1 /admin/models)"
    private_api="$(probe_status 10.9.8.1 /api/v1/admin/me)"
    public_login="$(probe_status 127.0.0.1 /login)"
    public_usage="$(probe_status 127.0.0.1 /api/v1/usage)"
    if [ "$public_page" = 403 ] && [ "$public_page_upper" = 403 ] && \
       [ "$public_api" = 403 ] && \
       [ "$private_page" = 200 ] && [ "$private_api" = 401 ] && \
       [ "$public_login" = 200 ] && [ "$public_usage" = 401 ]; then
        echo "Admin UI and API now accept only WireGuard clients"
        echo "Rollback configuration: $backup_conf"
        exit 0
    fi
    sleep 0.25
done

restore_previous
echo "Private admin probe failed (public page/uppercase/API $public_page/$public_page_upper/$public_api, private page/API $private_page/$private_api, login/usage $public_login/$public_usage); previous configuration restored" >&2
exit 1
