#!/usr/bin/env bash
# Run as root on Aliyun after the A6000 receiver uses its local callback proxy.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on Aliyun" >&2
    exit 1
fi

source_conf=/etc/nginx/conf.d/agent-af3-private.conf
disabled_conf=/etc/nginx/conf.d/agent-af3-private.conf.disabled-20261002
test -f "$source_conf"
if [ -e "$disabled_conf" ]; then
    echo "Disabled AF3 configuration already exists: $disabled_conf" >&2
    exit 1
fi

restore_previous() {
    mv -- "$disabled_conf" "$source_conf"
    nginx -t && systemctl reload nginx
}

mv -- "$source_conf" "$disabled_conf"
if ! nginx -t || ! systemctl reload nginx; then
    restore_previous
    echo "Nginx check or reload failed; old AF3 ingress restored" >&2
    exit 1
fi

if ! curl --noproxy '*' --resolve agent.bioailab.net:443:127.0.0.1 \
    --connect-timeout 5 --max-time 10 -fsS -o /dev/null \
    https://agent.bioailab.net/login; then
    restore_previous
    echo "Public site probe failed; old AF3 ingress restored" >&2
    exit 1
fi

api_status="$(curl --noproxy '*' --resolve agent.bioailab.net:443:127.0.0.1 \
    --connect-timeout 5 --max-time 10 -sS -o /dev/null -w '%{http_code}' \
    https://agent.bioailab.net/api/v1/usage)" || {
    restore_previous
    echo "API probe failed; old AF3 ingress restored" >&2
    exit 1
}
if [ "$api_status" != 401 ] || ss -lnt | grep -Fq '10.9.8.1:18184'; then
    restore_previous
    echo "API status or old AF3 listener check failed; ingress restored" >&2
    exit 1
fi

echo "Old Aliyun AF3 callback ingress disabled; A6000 local callback remains active"
