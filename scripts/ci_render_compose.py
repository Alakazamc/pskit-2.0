"""Render local/Staging/production Compose using temporary synthetic config only."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ci_new_stack import ROOT, isolated_env

AGENT = ROOT / "deploy/agent"
SUPABASE = ROOT / "infra/supabase"
LITELLM = ROOT / "infra/litellm"


def write_env(path: Path, values: dict[str, str]) -> Path:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)
    return path


def render(project: str, env_file: Path, overlays: list[Path], env: dict[str, str]) -> dict:
    command = ["docker", "compose", "--env-file", str(env_file), "-p", project]
    for overlay in overlays:
        command.extend(["-f", str(overlay)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(command, cwd=env_file.parent, env=env, capture_output=True,
                            text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"Compose render failed for {project}: {result.stderr}")
    return json.loads(result.stdout)


def fixture(directory: Path, target: str) -> Path:
    staging = target == "staging"
    network = "pskit-agent-supabase-staging_default" if staging else "pskit-agent-supabase_default"
    backend = write_env(directory / f"{target}.backend.env", {
        "RESEARCH_AGENT_DATABASE_URL": f"postgresql://pskit_app:ci-{target}@db:5432/postgres",
        "MODEL_GATEWAY_API_KEY": f"sk-ci-{target}-only",
        "SUPABASE_PUBLISHABLE_KEY": f"sb_publishable_ci_{target}",
        "RESEARCH_AGENT_AUTH_CSRF_SECRET": f"ci-{target}-csrf-distinct-secret-at-least-32-bytes",
        "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY": f"ci-{target}-callback-secret-at-least-32-bytes",
        "RESEARCH_AGENT_AUTH_ABUSE_MODE": "enforce" if target == "production" else "observe",
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET": f"ci-{target}-rate-limit-secret-at-least-32-bytes",
        "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON": '["127.0.0.1/32"]',
        "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED": "true" if target == "production" else "false",
        "TURNSTILE_SECRET_KEY": f"ci-{target}-turnstile-secret-placeholder",
        "TURNSTILE_HOSTNAMES_JSON": '["ci.example.invalid"]',
    })
    rollout = directory / f"{target}-rollout"
    rollout.mkdir()
    (rollout / "policy.json").write_text("{}")
    config = directory / f"{target}.opensandbox.toml"
    config.write_text("# CI rendering only; no service is started\n")
    values = {
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:ci",
        "AGENT_WEB_IMAGE": "pskit-agent-web:ci-unused",
        "STAGING_MODEL_IMAGE": "pskit-agent-backend:ci",
        "AGENT_BACKEND_ENV_FILE": str(backend),
        "AGENT_AF3_PROXY_KEY_FILE": str(backend),
        "AGENT_MCP_RECEIVER_ENV_FILE": str(backend),
        "SUPABASE_DOCKER_NETWORK": network,
        "AGENT_PG_DATA_VOLUME": f"pskit-ci-{target}-agent-data",
        "AGENT_MCP_RECEIVER_DATA_VOLUME": f"pskit-ci-{target}-receiver-data",
        "AGENT_PUBLIC_URL": "https://ci.example.invalid",
        "TURNSTILE_SITE_KEY": f"ci-{target}-turnstile-public-placeholder",
        "LITELLM_DB_PASSWORD": f"ci-{target}-gateway-password",
        "LITELLM_MASTER_KEY": f"sk-ci-{target}-master",
        "LITELLM_SALT_KEY": f"sk-ci-{target}-salt",
        "LITELLM_BIND_IP": "127.0.0.1",
        "LITELLM_PUBLIC_PORT": "4000",
        "OPENSANDBOX_SERVER_IMAGE": "pskit-opensandbox-server@sha256:" + "1" * 64,
        "OPENSANDBOX_SANDBOX_IMAGE": "pskit-workspace@sha256:" + "2" * 64,
        "OPENSANDBOX_API_KEY": f"ci-{target}-opensandbox-secret-at-least-32-bytes",
        "OPENSANDBOX_CONFIG_FILE": str(config),
        "OPENSANDBOX_ROLLOUT_DIR": str(rollout),
        "OPENSANDBOX_RUNTIME_NETWORK": f"pskit-ci-{target}-runtime",
        "OPENSANDBOX_STATE_VOLUME": f"pskit-ci-{target}-opensandbox-state",
        "OPENSANDBOX_VOLUME_DRIVER": "pskit-quota",
        "OPENSANDBOX_NAMESPACE": f"pskit-ci-{target}",
    }
    # Public template values are used only for shape; all secret fields are synthetic.
    for line in (SUPABASE / ".env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
    values.update(POSTGRES_PASSWORD=f"ci-{target}-supabase-password",
                  JWT_SECRET=f"ci-{target}-jwt-secret-at-least-32-bytes",
                  DASHBOARD_PASSWORD=f"ci-{target}-dashboard-password",
                  ANON_KEY=f"ci-{target}-anon-key", SERVICE_ROLE_KEY=f"ci-{target}-service-key",
                  SUPABASE_SECRET_KEY=f"sb_secret_ci_{target}",
                  OPENAI_API_KEY="", COMPOSE_FILE="")
    return write_env(directory / f"{target}.env", values)


def check_agent(model: dict, target: str, sandbox: bool = False) -> None:
    services = model["services"]
    backend = services["backend"]
    assert backend["environment"]["RESEARCH_AGENT_DATABASE_URL"].startswith(
        f"postgresql://pskit_app:ci-{target}@db:5432/"
    )
    for service in services.values():
        assert all(port.get("host_ip") == "127.0.0.1" for port in service.get("ports", []))
    if target == "local":
        assert backend["build"]["context"] == str(ROOT / "new_backend")
        return
    assert "build" not in backend
    assert "web" not in services
    assert "RESEARCH_AGENT_DB_PATH" not in backend["environment"]
    assert model["volumes"]["agent_data"]["name"] == f"pskit-ci-{target}-agent-data"
    assert backend["ports"][0]["published"] == ("18090" if target == "staging" else "18088")
    if target == "staging":
        assert backend["environment"]["RESEARCH_AGENT_AF3_EXECUTOR"] == "mock"
        assert "af3-callback-proxy" not in services
        assert model["networks"]["supabase"]["name"] == "pskit-agent-supabase-staging_default"
    if sandbox:
        assert services["opensandbox-server"]["read_only"] is True
        assert model["networks"]["opensandbox_runtime"]["internal"] is True
        assert model["networks"]["opensandbox_runtime"]["name"] == f"pskit-ci-{target}-runtime"
        assert model["volumes"]["opensandbox_state"]["name"] == f"pskit-ci-{target}-opensandbox-state"
        assert backend["environment"]["RESEARCH_AGENT_WORKSPACE_ROLLOUT_FILE"].endswith("policy.json")
        assert backend["environment"]["RESEARCH_AGENT_WORKSPACE_COMMANDS_ENABLED"] == "false"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pskit-compose-ci-") as temporary:
        directory = Path(temporary)
        env = isolated_env(directory, ROOT)
        rendered: dict[str, dict] = {}
        for target in ("local", "staging", "production"):
            env_file = fixture(directory, target)
            if target == "production":
                subprocess.run([sys.executable, str(AGENT / "scripts/production_config.py"),
                                "--backend-env-file", str(directory / "production.backend.env"),
                                "--cloud-env-file", str(env_file)], cwd=directory, env=env,
                               capture_output=True, text=True, check=True, timeout=30)
            agent = [AGENT / "compose.yaml"]
            if target == "local":
                agent.append(AGENT / "compose.local.yaml")
            else:
                agent.extend([AGENT / "compose.cloud.yaml", AGENT / "compose.postgres.yaml"])
                if target == "staging":
                    agent.append(AGENT / "compose.staging.yaml")
            model = render(f"pskit-ci-{target}", env_file, agent, env)
            check_agent(model, target)
            rendered[f"agent-{target}"] = model
            if target == "local":
                continue
            sandbox = render(f"pskit-ci-{target}", env_file, [*agent,
                             AGENT / "compose.opensandbox.yaml",
                             AGENT / f"compose.opensandbox.{target}.yaml"], env)
            check_agent(sandbox, target, sandbox=True)
            rendered[f"agent-{target}-opensandbox"] = sandbox
            supabase = [SUPABASE / "docker-compose.yml", SUPABASE / "compose.cloud.yaml"]
            gateway = [LITELLM / "compose.shared-postgres.yaml"]
            if target == "staging":
                supabase.append(SUPABASE / "compose.staging.yaml")
                gateway.append(LITELLM / "compose.staging.yaml")
            db_model = render(f"pskit-ci-supabase-{target}", env_file, supabase, env)
            assert db_model["services"]["db"]["image"].startswith("supabase/postgres:17.")
            assert all(port["host_ip"] == "127.0.0.1"
                       for service in db_model["services"].values() for port in service.get("ports", []))
            rendered[f"supabase-{target}"] = db_model
            gateway_model = render(f"pskit-ci-litellm-{target}", env_file, gateway, env)
            assert gateway_model["services"]["gateway"]["image"] == "ghcr.io/berriai/litellm:v1.100.3"
            rendered[f"litellm-{target}"] = gateway_model
        for kind in ("agent", "supabase", "litellm"):
            staging = rendered[f"{kind}-staging"]
            production = rendered[f"{kind}-production"]
            assert staging["name"] != production["name"]
            stage_volumes = {volume["name"] for volume in staging.get("volumes", {}).values()}
            prod_volumes = {volume["name"] for volume in production.get("volumes", {}).values()}
            assert stage_volumes.isdisjoint(prod_volumes)
        for name, model in rendered.items():
            print(f"Compose checked: {name} ({len(model['services'])} services)")


if __name__ == "__main__":
    main()
