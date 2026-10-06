"""Rendered staging Compose projects cannot resolve to production state."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/agent"
SUPABASE = ROOT / "infra/supabase"
LITELLM = ROOT / "infra/litellm"
STAGING_NETWORK = "pskit-agent-supabase-staging_default"


def _render(project: str, env_file: Path, overlays: list[Path], extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env.update(extra or {})
    command = ["docker", "compose", "--env-file", str(env_file)]
    for path in overlays:
        command += ["-f", str(path)]
    command += ["-p", project, "config", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_supabase_has_new_names_volumes_and_only_loopback_port():
    rendered = _render("pskit-agent-supabase-staging", SUPABASE / ".env.example", [
        SUPABASE / "docker-compose.yml", SUPABASE / "compose.cloud.yaml",
        SUPABASE / "compose.staging.yaml",
    ])
    assert rendered["name"] == "pskit-agent-supabase-staging"
    services = rendered["services"]
    names = [item["container_name"] for item in services.values() if "container_name" in item]
    assert len(names) == 11
    assert all(name.endswith("-staging") for name in names)
    assert services["api-gw"]["ports"] == [{
        "mode": "ingress", "host_ip": "127.0.0.1", "target": 8000,
        "published": "18131", "protocol": "tcp",
    }]
    assert not services["supavisor"].get("ports")
    assert rendered["volumes"]["agent-db-data"]["name"] == "pskit-agent-db-data-staging"
    assert rendered["volumes"]["agent-storage"]["name"] == "pskit-agent-storage-staging"
    assert not any("pskit-agent-db-data" == volume.get("name")
                   for volume in rendered["volumes"].values())
    assert all(port["host_ip"] == "127.0.0.1" for service in services.values()
               for port in service.get("ports", []))
    assert services["studio"]["volumes"][0]["type"] == "volume"
    assert services["functions"]["volumes"][0]["read_only"] is True


def test_litellm_uses_staging_database_and_mock_model(tmp_path):
    env_file = tmp_path / "litellm.env"
    env_file.write_text("\n".join([
        "LITELLM_DB_PASSWORD=staging-only-password",
        "LITELLM_MASTER_KEY=sk-staging-master",
        "LITELLM_SALT_KEY=sk-staging-salt",
        "LITELLM_BIND_IP=10.9.8.1",
        "LITELLM_PUBLIC_PORT=4002",
        f"SUPABASE_DOCKER_NETWORK={STAGING_NETWORK}",
    ]) + "\n")
    rendered = _render("pskit-agent-litellm-staging", env_file, [
        LITELLM / "compose.shared-postgres.yaml", LITELLM / "compose.staging.yaml",
    ], {"STAGING_MODEL_IMAGE": "pskit-agent-backend:test-fixed"})
    assert rendered["name"] == "pskit-agent-litellm-staging"
    assert rendered["networks"]["supabase"]["name"] == STAGING_NETWORK
    assert set(rendered["services"]) == {"gateway", "model-stub"}
    gateway = rendered["services"]["gateway"]
    assert gateway["ports"][0]["host_ip"] == "10.9.8.1"
    assert gateway["ports"][0]["published"] == "4002"
    assert "://litellm:staging-only-password@db:5432/litellm" in gateway["environment"]["DATABASE_URL"]
    assert gateway["volumes"][0]["source"] == str(LITELLM / "config.staging.yaml")
    assert not rendered["services"]["model-stub"].get("ports")
    assert "model_name: claude-opus-4-8" in (LITELLM / "config.staging.yaml").read_text()
    assert "http://model-stub:8000/v1" in (LITELLM / "config.staging.yaml").read_text()


def test_agent_is_private_and_af3_is_mock():
    rendered = _render("pskit-agent-staging", DEPLOY / "cloud.env.example", [
        DEPLOY / "compose.yaml", DEPLOY / "compose.cloud.yaml",
        DEPLOY / "compose.postgres.yaml", DEPLOY / "compose.staging.yaml",
    ], {
        "AGENT_BACKEND_ENV_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_AF3_PROXY_KEY_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_MCP_RECEIVER_ENV_FILE": str(DEPLOY / "mcp.receiver.env.example"),
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:test-fixed",
        "AGENT_PG_DATA_VOLUME": "pskit-agent-staging_agent_data",
        "AGENT_MCP_RECEIVER_DATA_VOLUME": "pskit-agent-staging_mcp_receiver_data",
        "SUPABASE_DOCKER_NETWORK": STAGING_NETWORK,
    })
    assert rendered["name"] == "pskit-agent-staging"
    assert rendered["networks"]["supabase"]["name"] == STAGING_NETWORK
    assert rendered["volumes"]["agent_data"]["name"] == "pskit-agent-staging_agent_data"
    assert (
        rendered["volumes"]["mcp_receiver_data"]["name"]
        == "pskit-agent-staging_mcp_receiver_data"
    )
    backend = rendered["services"]["backend"]
    assert backend["ports"][0]["host_ip"] == "127.0.0.1"
    assert backend["ports"][0]["published"] == "18090"
    assert backend["environment"]["RESEARCH_AGENT_AF3_EXECUTOR"] == "mock"
    assert backend["environment"]["RESEARCH_AGENT_AUTH_COOKIE_SECURE"] == "false"
    assert "RESEARCH_AGENT_DB_PATH" not in backend["environment"]
    assert "10.9.8.2" not in json.dumps(rendered)
    assert "web" not in rendered["services"]
    assert "af3-callback-proxy" not in rendered["services"]
    receiver = rendered["services"]["mcp-receiver"]
    assert not receiver.get("ports")
    assert receiver["read_only"] is True
