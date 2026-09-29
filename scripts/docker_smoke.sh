#!/usr/bin/env sh
set -eu

base_url="${1:-http://127.0.0.1:10716}"
command -v python3 >/dev/null 2>&1 || {
  echo "docker_smoke.sh requires python3 to encode authentication requests safely" >&2
  exit 2
}
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

health_file="$tmp_dir/health.json"
index_file="$tmp_dir/index.html"
cookie_file="$tmp_dir/cookies.txt"
response_file="$tmp_dir/response.json"

curl --fail --silent --show-error --max-time 10 \
  "$base_url/api/health" >"$health_file"
grep -q '"ok":true' "$health_file"

curl --fail --silent --show-error --max-time 10 \
  "$base_url/agent" >"$index_file"
grep -q 'PSKit 2.0' "$index_file"

asset_path="$(sed -n 's/.*src="\([^"]*\/assets\/[^"]*\.js\)".*/\1/p' "$index_file" | head -n 1)"
if [ -n "$asset_path" ]; then
  curl --fail --silent --show-error --max-time 10 \
    "$base_url$asset_path" >/dev/null
fi

username="${PSKIT_SMOKE_USERNAME:-smoke_$(date +%s)_$$}"
password="${PSKIT_SMOKE_PASSWORD:-SmokePass_12345}"
registration="$(curl --fail --silent --show-error --max-time 10 \
  "$base_url/api/auth/registration")"
auth_payload="$(PSKIT_SMOKE_PAYLOAD_USERNAME="$username" PSKIT_SMOKE_PAYLOAD_PASSWORD="$password" \
  python3 -c 'import json,os; print(json.dumps({"username":os.environ["PSKIT_SMOKE_PAYLOAD_USERNAME"],"password":os.environ["PSKIT_SMOKE_PAYLOAD_PASSWORD"]}))')"

if [ -z "${PSKIT_SMOKE_USERNAME:-}" ] && printf '%s' "$registration" | grep -q '"enabled":true'; then
  register_payload="$auth_payload"
  if printf '%s' "$registration" | grep -q '"requires_bootstrap_token":true'; then
    if [ -z "${PSKIT_SMOKE_BOOTSTRAP_TOKEN:-}" ]; then
      echo "First administrator requires PSKIT_SMOKE_BOOTSTRAP_TOKEN; alternatively create an administrator first and provide an existing PSKIT_SMOKE_USERNAME/PSKIT_SMOKE_PASSWORD." >&2
      exit 2
    fi
    register_payload="$(printf '%s' "$auth_payload" | python3 -c 'import json,os,sys; payload=json.load(sys.stdin); payload["bootstrap_token"]=os.environ["PSKIT_SMOKE_BOOTSTRAP_TOKEN"]; print(json.dumps(payload))')"
  fi
  status="$(curl --silent --show-error --max-time 10 \
    --cookie-jar "$cookie_file" \
    --header 'Content-Type: application/json' \
    --data "$register_payload" \
    --output "$response_file" --write-out '%{http_code}' \
    "$base_url/api/auth/register")"
  test "$status" = "200"
  grep -q "\"username\":\"$username\"" "$response_file"
else
  status="$(curl --silent --show-error --max-time 10 \
    --cookie-jar "$cookie_file" \
    --header 'Content-Type: application/json' \
    --data "$auth_payload" \
    --output "$response_file" --write-out '%{http_code}' \
    "$base_url/api/auth/login")"
  test "$status" = "200"
fi

curl --fail --silent --show-error --max-time 10 \
  --cookie "$cookie_file" "$base_url/api/auth/me" >"$response_file"
grep -q "\"username\":\"$username\"" "$response_file"

curl --fail --silent --show-error --max-time 10 \
  --cookie "$cookie_file" "$base_url/api/tasks" >"$response_file"
grep -q '^\[' "$response_file"

curl --fail --silent --show-error --max-time 10 \
  --cookie "$cookie_file" --cookie-jar "$cookie_file" \
  --request POST "$base_url/api/auth/logout" >"$response_file"
grep -q '"ok":true' "$response_file"

status="$(curl --silent --show-error --max-time 10 \
  --cookie "$cookie_file" --output "$response_file" --write-out '%{http_code}' \
  "$base_url/api/auth/me")"
test "$status" = "401"

curl --fail --silent --show-error --max-time 10 \
  --cookie-jar "$cookie_file" \
  --header 'Content-Type: application/json' \
  --data "$auth_payload" \
  "$base_url/api/auth/login" >"$response_file"
curl --fail --silent --show-error --max-time 10 \
  --cookie "$cookie_file" "$base_url/api/auth/me" >"$response_file"
grep -q "\"username\":\"$username\"" "$response_file"

echo "health=ok"
echo "spa=ok"
echo "asset=ok"
echo "auth=ok"
echo "scope=health,frontend,session-auth,task-list; real model inference is not exercised"
