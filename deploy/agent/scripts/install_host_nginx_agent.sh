#!/usr/bin/env bash
# Run as root on Aliyun only after the A6000 private API returns HTTP 401.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on Aliyun" >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
dist_dir="$script_dir/../../../frontend-dist"
source_conf="$script_dir/../host-nginx-agent-split.conf"
web_root=/var/www/agent.bioailab.net
target_conf=/etc/nginx/conf.d/agent.bioailab.net.conf
backup_conf=/etc/nginx/conf.d/agent.bioailab.net.conf.pre-a6000

test -f "$dist_dir/index.html"
test -f "$source_conf"
test -f "$target_conf"
if [ -e "$backup_conf" ]; then
    echo "Existing backup must be reviewed before running again: $backup_conf" >&2
    exit 1
fi

# The private API must be live before moving the public /api/v1/ route.
status="$(curl --noproxy '*' --connect-timeout 5 --max-time 10 -sS \
    -o /dev/null -w '%{http_code}' -H 'Host: agent.bioailab.net' \
    http://10.9.8.2:18088/api/v1/usage)"
if [ "$status" != 401 ]; then
    echo "A6000 private API preflight failed (HTTP $status)" >&2
    exit 1
fi

install -d -m 0755 "$web_root"
cp -a "$dist_dir/." "$web_root/"
chmod -R a+rX "$web_root"
cp -a "$target_conf" "$backup_conf"
install -m 0644 "$source_conf" "$target_conf"

if ! nginx -t; then
    cp -a "$backup_conf" "$target_conf"
    nginx -t
    echo "Nginx syntax failed; original virtual host restored" >&2
    exit 1
fi
if ! systemctl reload nginx; then
    cp -a "$backup_conf" "$target_conf"
    nginx -t && systemctl reload nginx
    echo "Nginx reload failed; original virtual host restored" >&2
    exit 1
fi

curl --noproxy '*' --resolve agent.bioailab.net:443:127.0.0.1 \
    --connect-timeout 5 --max-time 10 -fsS -o /dev/null \
    https://agent.bioailab.net/login
echo "Host Nginx now serves React dist and routes /api/v1/ to A6000"
