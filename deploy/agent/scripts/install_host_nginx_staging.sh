#!/usr/bin/env bash
# Install only the WireGuard staging vhost. Never edit the production vhost.
set -Eeuo pipefail

agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repo_dir=$(cd -- "$agent_dir/../.." && pwd)
config_dir=${STAGING_CONFIG_DIR:-/home/ecs-user/pskit-agent-staging-private}
replace=false
if [[ ${1:-} == --replace-staging && $# == 1 ]]; then
  replace=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: install_host_nginx_staging.sh [--replace-staging]" >&2
  exit 2
fi

if [[ -n ${STAGING_TEST_ROOT:-} ]]; then
  test_root=$(realpath -- "$STAGING_TEST_ROOT")
  [[ $test_root == /tmp/* ]] || { echo "Test root must be under /tmp" >&2; exit 2; }
  conf_dir="$test_root/etc/nginx/conf.d"
  web_parent="$test_root/var/www"
else
  [[ $(id -u) -eq 0 ]] || { echo "Run as root on Aliyun" >&2; exit 2; }
  conf_dir=/etc/nginx/conf.d
  web_parent=/var/www
fi

if ! ip -o -4 address show | grep -Eq '(^|[[:space:]])10\.9\.8\.1/'; then
  echo "WireGuard address 10.9.8.1 is unavailable" >&2
  exit 2
fi

[[ -d $config_dir && ! -L $config_dir ]] || { echo "Private staging directory is missing" >&2; exit 2; }
[[ -f $config_dir/manifest.json && ! -L $config_dir/manifest.json ]] || {
  echo "Staging release manifest is missing" >&2; exit 2;
}
[[ $(stat -c %a "$config_dir") == 700 && $(stat -c %a "$config_dir/manifest.json") == 600 ]] || {
  echo "Staging release manifest has unsafe permissions" >&2; exit 2;
}

dist_dir=$(PYTHONPATH="$repo_dir${PYTHONPATH:+:$PYTHONPATH}" python - "$config_dir/manifest.json" <<'PY'
import json
import sys
from pathlib import Path
from deploy.agent.scripts.prepare_staging import _dist_hash

manifest = json.loads(Path(sys.argv[1]).read_text())
dist = Path(manifest["frontend_dist"]).resolve(strict=True)
if not (dist / "index.html").is_file() or _dist_hash(dist) != manifest["frontend_dist_sha256"]:
    raise SystemExit("Staging frontend dist differs from release manifest")
print(dist)
PY
)
source_conf="$agent_dir/host-nginx-agent-staging.conf"
target_conf="$conf_dir/agent-staging-private.conf"
web_root="$web_parent/agent-staging"
production_conf="$conf_dir/agent.bioailab.net.conf"
[[ -f $source_conf ]] || { echo "Staging Nginx source is missing" >&2; exit 2; }
[[ -f $production_conf ]] || { echo "Production Nginx vhost is missing" >&2; exit 2; }
if [[ -e $target_conf && $replace != true ]]; then
  echo "Staging Nginx vhost already exists; pass --replace-staging" >&2
  exit 2
fi
if [[ -L $target_conf || -L $web_root ]]; then
  echo "Staging Nginx destination is unsafe" >&2
  exit 2
fi

install -d -m 0755 "$conf_dir" "$web_parent"
temp=$(mktemp -d "$web_parent/.agent-staging-install.XXXXXX")
prior_conf=false
prior_web=false
cleanup() { rm -rf -- "$temp"; }
trap cleanup EXIT
cp -a -- "$dist_dir/." "$temp/site/"
chmod -R a+rX "$temp/site"
if [[ -n ${STAGING_TEST_ROOT:-} ]]; then
  sed "s#/var/www/agent-staging#$web_root#g" "$source_conf" > "$temp/vhost.conf"
else
  cp -- "$source_conf" "$temp/vhost.conf"
fi
chmod 0644 "$temp/vhost.conf"

if [[ -e $target_conf ]]; then
  cp -a -- "$target_conf" "$temp/old-vhost.conf"
  prior_conf=true
fi
if [[ -e $web_root ]]; then
  mv -- "$web_root" "$temp/old-site"
  prior_web=true
fi
mv -- "$temp/site" "$web_root"
cp -- "$temp/vhost.conf" "$target_conf"
chmod 0644 "$target_conf"

restore_staging() {
  if [[ $prior_conf == true ]]; then
    cp -- "$temp/old-vhost.conf" "$target_conf"
  else
    rm -f -- "$target_conf"
  fi
  rm -rf -- "$web_root"
  if [[ $prior_web == true ]]; then
    mv -- "$temp/old-site" "$web_root"
  fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
}

if ! nginx -t >/dev/null 2>&1; then
  restore_staging
  echo "Nginx validation failed; previous Staging vhost restored" >&2
  exit 2
fi
if ! systemctl reload nginx >/dev/null 2>&1; then
  restore_staging
  echo "Nginx reload failed; previous Staging vhost restored" >&2
  exit 2
fi
echo "WireGuard Staging site now serves the locked React dist"
