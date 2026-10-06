"""The cloud launcher starts three Compose projects around one PostgreSQL."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/agent"
STACK = DEPLOY / "stack.sh"
OVERLAY = DEPLOY / "compose.postgres.yaml"


@pytest.fixture
def stack_env(tmp_path):
    def private(name, lines):
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)
        return path

    supabase = private("supabase.env", [
        "POSTGRES_PASSWORD=local-test-password",
        "CLOUD_DISABLE_SIGNUP=true",
    ])
    litellm = private("litellm.env", [
        "LITELLM_DB_PASSWORD=local-litellm-test-password",
        "LITELLM_MASTER_KEY=sk-local-test-master",
        "LITELLM_SALT_KEY=sk-local-test-salt",
        "LITELLM_BIND_IP=127.0.0.1",
        "LITELLM_PUBLIC_PORT=4000",
        "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase_default",
    ])
    backend = private("backend.env", [
        "RESEARCH_AGENT_DATABASE_URL=postgresql://pskit_app:local-pskit-test-password@db:5432/postgres",
        "MODEL_GATEWAY_API_KEY=sk-local-test-key",
        "MODEL_GATEWAY_MODEL=pskit-smoke",
        "SUPABASE_PUBLISHABLE_KEY=sb_publishable_local",
        "RESEARCH_AGENT_AUTH_ABUSE_MODE=observe",
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET=local-auth-rate-limit-secret-at-least-32-bytes",
        'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["127.0.0.1/32"]',
        "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED=true",
        "TURNSTILE_SECRET_KEY=production-turnstile-secret-test-value",
        'TURNSTILE_HOSTNAMES_JSON=["agent.bioailab.net"]',
    ])
    admin = private("admin.env", [
        "SHARED_POSTGRES_ADMIN_DSN=postgresql://postgres:local-test-password@db:5432/postgres",
        "PSKIT_DB_PASSWORD=local-pskit-test-password",
        "LITELLM_DB_PASSWORD=local-litellm-test-password",
    ])
    proxy = private("proxy.env", ["RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=local-test-key"])
    cloud = private("cloud.env", [
        "AGENT_BACKEND_IMAGE=pskit-agent-backend:test-fixed",
        "AGENT_WEB_IMAGE=pskit-agent-web:unused-fixed",
        "TURNSTILE_SITE_KEY=production-turnstile-site-key",
        f"AGENT_BACKEND_ENV_FILE={backend}",
        f"AGENT_AF3_PROXY_KEY_FILE={proxy}",
    ])
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    fake_docker = fake_dir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "if [[ -n ${FAKE_FAIL_ON:-} && \"$*\" == *\"$FAKE_FAIL_ON\"* ]]; then exit 29; fi\n"
        "if [[ $1 == ps && \"$*\" == *pskit-agent-supabase* ]]; then "
        "printf '%s\\n' \"${FAKE_SUPABASE_DB:-test-supabase-db}\"; fi\n"
        "if [[ $1 == ps && \"$*\" == *pskit-agent-litellm* ]]; then "
        "printf '%s' \"${FAKE_OLD_DB:-}\"; fi\n"
    )
    fake_docker.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {"PATH": os.environ["PATH"]}
    env.update({
        "PATH": f"{fake_dir}:{env['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "STACK_SUPABASE_ENV_FILE": str(supabase),
        "STACK_LITELLM_ENV_FILE": str(litellm),
        "STACK_BACKEND_ENV_FILE": str(backend),
        "STACK_ADMIN_ENV_FILE": str(admin),
        "STACK_CLOUD_ENV_FILE": str(cloud),
        "STACK_PROXY_ENV_FILE": str(proxy),
        "STACK_BACKEND_IMAGE": "pskit-agent-backend:test-fixed",
        "STACK_SUPABASE_NETWORK": "pskit-agent-supabase_default",
    })
    return env, log, backend


def run_stack(stack_env, *args):
    env, log, _backend = stack_env
    result = subprocess.run(
        ["bash", str(STACK), *args], cwd=env["STACK_CLOUD_ENV_FILE"].rsplit("/", 1)[0],
        env=env, capture_output=True, text=True, check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_up_orders_supabase_litellm_migration_backend(stack_env):
    result, calls = run_stack(stack_env, "up")
    assert result.returncode == 0, result.stderr
    assert next(i for i, call in enumerate(calls) if "supabase" in call and "up -d --wait" in call) < next(
        i for i, call in enumerate(calls) if "provision_shared_postgres.py" in call
    ) < next(i for i, call in enumerate(calls) if "litellm" in call and "up -d --wait" in call) < next(
        i for i, call in enumerate(calls) if "migrate_postgres" in call
    ) < next(i for i, call in enumerate(calls) if "backend af3-callback-proxy" in call)
    assert not any("up -d --wait web" in call for call in calls)
    assert "local-pskit-test-password" not in result.stdout + result.stderr


def test_missing_key_or_gateway_failure_never_starts_backend(stack_env):
    env, _log, backend = stack_env
    backend.write_text("RESEARCH_AGENT_DATABASE_URL=postgresql://pskit_app:x@db:5432/postgres\n")
    result, calls = run_stack(stack_env, "up")
    assert result.returncode != 0
    assert not any("up -d --wait backend" in call for call in calls)
    backend.write_text(
        "RESEARCH_AGENT_DATABASE_URL=postgresql://pskit_app:x@db:5432/postgres\n"
        "MODEL_GATEWAY_API_KEY=sk-local-test-key\n"
    )
    env["FAKE_FAIL_ON"] = "pskit-agent-litellm up -d --wait"
    result, calls = run_stack(stack_env, "up")
    assert result.returncode != 0
    assert not any("up -d --wait backend" in call for call in calls)


def test_missing_auth_protection_secret_never_runs_docker(stack_env):
    _env, _log, backend = stack_env
    backend.write_text(backend.read_text().replace(
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET=local-auth-rate-limit-secret-at-least-32-bytes\n",
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET=replace-with-secret\n",
    ))
    result, calls = run_stack(stack_env, "up")
    assert result.returncode != 0
    assert "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET" in result.stderr
    assert calls == []


@pytest.mark.parametrize(
    "file_index,old,new,error_key",
    [
        (0, 'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["127.0.0.1/32"]',
         'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["0.0.0.0/0"]',
         "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON"),
        (0, "TURNSTILE_SECRET_KEY=production-turnstile-secret-test-value",
         "TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA",
         "TURNSTILE_SECRET_KEY"),
        (1, "TURNSTILE_SITE_KEY=production-turnstile-site-key",
         "TURNSTILE_SITE_KEY=1x00000000000000000000AA",
         "TURNSTILE_SITE_KEY"),
    ],
)
def test_unsafe_production_auth_values_never_run_docker(
    stack_env, file_index, old, new, error_key,
):
    env, _log, backend = stack_env
    target = backend if file_index == 0 else Path(env["STACK_CLOUD_ENV_FILE"])
    target.write_text(target.read_text().replace(old, new))
    result, calls = run_stack(stack_env, "up")
    assert result.returncode != 0
    assert error_key in result.stderr
    assert calls == []


def test_final_status_has_one_postgres(stack_env):
    result, calls = run_stack(stack_env, "status")
    assert result.returncode == 0, result.stderr
    assert "supabase-db: 1" in result.stdout
    assert "legacy-litellm-db: 0" in result.stdout
    assert any("pskit-agent-supabase" in call and "service=db" in call for call in calls)
    assert any("pskit-agent-litellm" in call and "service=db" in call for call in calls)
    assert sum(call.startswith("compose ") and call.endswith(" ps") for call in calls) == 3
    stack_env[0]["FAKE_OLD_DB"] = "old-litellm-db"
    result, _calls = run_stack(stack_env, "status")
    assert result.returncode != 0
    assert "legacy-litellm-db: 1" in result.stdout


def test_render_has_no_public_db_port_and_up_excludes_web():
    env = {"PATH": os.environ["PATH"]}
    env.update({
        "AGENT_BACKEND_ENV_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_AF3_PROXY_KEY_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:test-fixed",
        "AGENT_WEB_IMAGE": "pskit-agent-web:unused-fixed",
    })
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(DEPLOY / "cloud.env.example"),
         "-f", str(DEPLOY / "compose.yaml"),
         "-f", str(DEPLOY / "compose.cloud.yaml"),
         "-f", str(OVERLAY), "config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    rendered = json.loads(result.stdout)
    assert rendered["services"]["backend"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "web" not in rendered["services"]
    assert "RESEARCH_AGENT_DB_PATH" not in rendered["services"]["backend"]["environment"]
    assert not any(
        port.get("target") == 5432 or port.get("published") == "5432"
        for service in rendered["services"].values() for port in service.get("ports", [])
    )


def test_down_never_uses_volume_flag(stack_env):
    result, calls = run_stack(stack_env, "down")
    assert result.returncode == 0, result.stderr
    down = [call for call in calls if " down" in call]
    assert len(down) == 3
    assert "agent" in down[0] and "litellm" in down[1] and "supabase" in down[2]
    assert all(" -v" not in call and "--volumes" not in call for call in down)
