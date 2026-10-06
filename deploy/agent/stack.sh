#!/usr/bin/env bash
# One operational entry point for the existing Supabase, LiteLLM and Agent projects.
set -Eeuo pipefail

agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$agent_dir/../.." && pwd)

die() { echo "$*" >&2; exit 2; }

private_file() {
  local file=$1 mode
  [[ -f "$file" && ! -L "$file" ]] || die "Missing or unsafe private configuration file"
  mode=$(stat -c %a "$file")
  (( (8#$mode & 8#077) == 0 )) || die "Private configuration has permissive mode"
}

if [[ -e "$agent_dir/.env.stack" ]]; then
  private_file "$agent_dir/.env.stack"
  # The operator-owned file may set paths and image tags, not provider keys.
  set -a
  # shellcheck disable=SC1091
  source "$agent_dir/.env.stack"
  set +a
fi

supabase_env=${STACK_SUPABASE_ENV_FILE:-$repo_dir/infra/supabase/.env}
litellm_env=${STACK_LITELLM_ENV_FILE:-$repo_dir/infra/litellm/.env.shared}
backend_env=${STACK_BACKEND_ENV_FILE:-$agent_dir/cloud.backend.env}
admin_env=${STACK_ADMIN_ENV_FILE:-$agent_dir/.env.stack-admin}
cloud_env=${STACK_CLOUD_ENV_FILE:-$agent_dir/cloud.env}
proxy_env=${STACK_PROXY_ENV_FILE:-$agent_dir/cloud.proxy.env}
supabase_network=${STACK_SUPABASE_NETWORK:-pskit-agent-supabase_default}

value_in_file() {
  local key=$2 line
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$key="* ]]; then
      printf '%s' "${line#*=}"
      return 0
    fi
  done < "$1"
  return 1
}

require_value() {
  local value
  value=$(value_in_file "$1" "$2") || die "Required configuration is missing: $2"
  [[ -n "$value" && "$value" != *replace-with* ]] || die "Required configuration is unset: $2"
}

supabase=(docker compose --env-file "$supabase_env"
  -f "$repo_dir/infra/supabase/docker-compose.yml"
  -f "$repo_dir/infra/supabase/compose.cloud.yaml"
  -p pskit-agent-supabase)
litellm=(docker compose --env-file "$litellm_env"
  -f "$repo_dir/infra/litellm/compose.shared-postgres.yaml"
  -p pskit-agent-litellm)
agent=(docker compose --env-file "$cloud_env"
  -f "$agent_dir/compose.yaml"
  -f "$agent_dir/compose.cloud.yaml"
  -f "$agent_dir/compose.postgres.yaml"
  -p pskit-agent-cloud)

backend_image=${STACK_BACKEND_IMAGE:-}

preflight() {
  local file
  for file in "$supabase_env" "$litellm_env" "$backend_env" "$admin_env" "$cloud_env" "$proxy_env"; do
    private_file "$file"
  done
  require_value "$admin_env" SHARED_POSTGRES_ADMIN_DSN
  require_value "$admin_env" LITELLM_DB_PASSWORD
  require_value "$admin_env" PSKIT_DB_PASSWORD
  require_value "$backend_env" RESEARCH_AGENT_DATABASE_URL
  require_value "$backend_env" MODEL_GATEWAY_API_KEY
  require_value "$backend_env" MODEL_GATEWAY_MODEL
  require_value "$backend_env" RESEARCH_AGENT_AUTH_ABUSE_MODE
  require_value "$backend_env" RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET
  require_value "$backend_env" RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON
  require_value "$backend_env" RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED
  require_value "$backend_env" TURNSTILE_SECRET_KEY
  require_value "$backend_env" TURNSTILE_HOSTNAMES_JSON
  require_value "$cloud_env" TURNSTILE_SITE_KEY
  require_value "$litellm_env" LITELLM_DB_PASSWORD
  require_value "$litellm_env" LITELLM_MASTER_KEY
  require_value "$litellm_env" LITELLM_SALT_KEY
  require_value "$proxy_env" RESEARCH_AGENT_COMPUTE_CALLBACK_KEY
  auth_mode=$(value_in_file "$backend_env" RESEARCH_AGENT_AUTH_ABUSE_MODE)
  [[ "$auth_mode" == observe || "$auth_mode" == enforce ]] ||
    die "RESEARCH_AGENT_AUTH_ABUSE_MODE must be observe or enforce"
  auth_secret=$(value_in_file "$backend_env" RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET)
  (( ${#auth_secret} >= 32 )) || die "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET is too short"
  [[ $(value_in_file "$backend_env" RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON) == '["127.0.0.1/32"]' ]] ||
    die "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON must trust only the local Nginx proxy"
  [[ $(value_in_file "$backend_env" RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED) == true ]] ||
    die "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED must be true"
  [[ $(value_in_file "$backend_env" TURNSTILE_SECRET_KEY) != 1x0000000000000000000000000000000AA ]] ||
    die "TURNSTILE_SECRET_KEY must not use the public test secret"
  [[ $(value_in_file "$cloud_env" TURNSTILE_SITE_KEY) != 1x00000000000000000000AA ]] ||
    die "TURNSTILE_SITE_KEY must not use the public test site key"
  if [[ $(value_in_file "$supabase_env" CLOUD_DISABLE_SIGNUP 2>/dev/null || printf true) == false ]]; then
    require_value "$supabase_env" SMTP_HOST
    require_value "$supabase_env" SMTP_USER
    require_value "$supabase_env" SMTP_PASS
    require_value "$supabase_env" SMTP_ADMIN_EMAIL
  fi
  if [[ -z "$backend_image" ]]; then
    backend_image=$(value_in_file "$cloud_env" AGENT_BACKEND_IMAGE) || die "AGENT_BACKEND_IMAGE is missing"
  fi
  [[ -n "$backend_image" && "$backend_image" != *replace-with* ]] || die "Backend image is unset"
  [[ $(value_in_file "$backend_env" RESEARCH_AGENT_DATABASE_URL) == postgresql://pskit_app:* ]] ||
    die "Backend must use the restricted pskit_app PostgreSQL role"
  [[ $(value_in_file "$litellm_env" LITELLM_PUBLIC_PORT) == 4000 ]] ||
    die "Final LiteLLM gateway must publish port 4000"
  [[ $(value_in_file "$litellm_env" SUPABASE_DOCKER_NETWORK) == "$supabase_network" ]] ||
    die "LiteLLM and Agent must join the same Supabase Docker network"
  export AGENT_BACKEND_IMAGE="$backend_image"
  export AGENT_BACKEND_ENV_FILE="$backend_env"
  export AGENT_AF3_PROXY_KEY_FILE="$proxy_env"
  export SUPABASE_DOCKER_NETWORK="$supabase_network"
  docker image inspect "$backend_image" >/dev/null
  "${supabase[@]}" config --quiet
  "${litellm[@]}" config --quiet
  "${agent[@]}" config --quiet
}

running_db_count() {
  local project=$1 output
  output=$(docker ps --filter "label=com.docker.compose.project=$project" \
    --filter label=com.docker.compose.service=db --format '{{.ID}}')
  if [[ -z "$output" ]]; then
    printf '0'
  else
    printf '%s\n' "$output" | wc -l | tr -d ' '
  fi
}

command_name=${1:-}
case "$command_name" in
  up)
    preflight
    [[ $(running_db_count pskit-agent-litellm) == 0 ]] ||
      die "Stop the legacy LiteLLM PostgreSQL 16 container before final stack up"
    "${supabase[@]}" up -d --wait
    docker run --rm --network "$supabase_network" --env-file "$admin_env" \
      --read-only --cap-drop ALL --security-opt no-new-privileges \
      -v "$agent_dir/scripts/provision_shared_postgres.py:/app/provision_shared_postgres.py:ro" \
      --entrypoint python "$backend_image" /app/provision_shared_postgres.py
    "${litellm[@]}" up -d --wait
    docker run --rm --network "$supabase_network" --env-file "$admin_env" \
      --read-only --cap-drop ALL --security-opt no-new-privileges \
      --entrypoint python "$backend_image" -c \
      'import os; from app.db.postgres_migrations import migrate_postgres; migrate_postgres(os.environ["SHARED_POSTGRES_ADMIN_DSN"])'
    if ! "${agent[@]}" up -d --wait backend af3-callback-proxy; then
      "${agent[@]}" stop af3-callback-proxy backend || true
      die "Agent startup failed; databases and volumes were retained"
    fi
    [[ $(running_db_count pskit-agent-supabase) == 1 ]] || die "Supabase PostgreSQL count is not one"
    [[ $(running_db_count pskit-agent-litellm) == 0 ]] || die "Legacy LiteLLM database is still running"
    echo "Agent stack healthy with one Supabase PostgreSQL container"
    ;;
  status)
    supabase_count=$(running_db_count pskit-agent-supabase)
    legacy_count=$(running_db_count pskit-agent-litellm)
    printf 'supabase-db: %s\nlegacy-litellm-db: %s\n' "$supabase_count" "$legacy_count"
    printf '\n[Supabase]\n'
    "${supabase[@]}" ps
    printf '\n[LiteLLM]\n'
    "${litellm[@]}" ps
    printf '\n[Agent]\n'
    "${agent[@]}" ps
    [[ "$supabase_count" == 1 && "$legacy_count" == 0 ]] || exit 1
    ;;
  logs)
    case "${2:-}" in
      supabase) "${supabase[@]}" logs --tail=100 ;;
      litellm) "${litellm[@]}" logs --tail=100 ;;
      agent) "${agent[@]}" logs --tail=100 backend af3-callback-proxy ;;
      *) die "Usage: stack.sh logs supabase|litellm|agent" ;;
    esac
    ;;
  down)
    "${agent[@]}" down
    "${litellm[@]}" down
    "${supabase[@]}" down
    ;;
  *) die "Usage: stack.sh up|status|logs <project>|down" ;;
esac
