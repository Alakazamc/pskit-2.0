from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "agent"


def _render(*, staging: bool, profiles: tuple[str, ...] = ()) -> dict:
    env = dict(os.environ)
    env.update({
        "AGENT_BACKEND_ENV_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_MCP_RECEIVER_ENV_FILE": str(DEPLOY / "mcp.receiver.env.example"),
        "AGENT_AF3_PROXY_KEY_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:tool-products-test",
        "AGENT_WEB_IMAGE": "pskit-agent-web:tool-products-test",
        "AGENT_PG_DATA_VOLUME": "pskit-agent-test-data",
        "AGENT_MCP_RECEIVER_DATA_VOLUME": "pskit-agent-test-mcp-receiver",
        "SUPABASE_DOCKER_NETWORK": "pskit-agent-test-supabase",
    })
    files = [DEPLOY / "compose.yaml", DEPLOY / "compose.cloud.yaml", DEPLOY / "compose.postgres.yaml"]
    if staging:
        files.append(DEPLOY / "compose.staging.yaml")
    command = ["docker", "compose"]
    for profile in profiles:
        command += ["--profile", profile]
    command += [item for path in files for item in ("-f", str(path))]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_production_receiver_requires_an_explicit_profile():
    assert "mcp-receiver" not in _render(staging=False)["services"]
    assert "mcp-receiver" in _render(staging=False, profiles=("mcp-receiver",))["services"]


def test_staging_receiver_is_private_pinned_and_durable():
    rendered = _render(staging=True)
    receiver = rendered["services"]["mcp-receiver"]

    assert receiver["image"] == "pskit-agent-backend:tool-products-test"
    assert not receiver["image"].endswith(":latest")
    assert receiver["restart"] == "unless-stopped"
    assert receiver["read_only"] is True
    assert receiver["cap_drop"] == ["ALL"]
    assert not receiver.get("ports")
    assert receiver["environment"]["PSKIT_MCP_BACKEND_URL"] == "http://backend:8000"
    assert receiver["environment"]["PSKIT_MCP_SERVICE_ID"] == "coral-mcp"
    assert "mcp_compute_receiver.py" in " ".join(receiver["command"])
    assert "/var/lib/pskit-mcp/journal.sqlite3" in receiver["command"]
    journal = next(item for item in receiver["volumes"] if item["target"] == "/var/lib/pskit-mcp")
    assert journal["type"] == "volume"
    assert rendered["volumes"][journal["source"]]["name"] == "pskit-agent-test-mcp-receiver"
    assert set(receiver["networks"]) == {"app", "mcp_egress"}


def test_receiver_configuration_uses_references_and_keeps_credentials_out_of_compose():
    example = (DEPLOY / "mcp.receiver.env.example").read_text(encoding="utf-8")
    assert "PSKIT_COMPUTE_SERVICE_KEY=" in example
    assert "PSKIT_MCP_ENDPOINT_OVERRIDES_JSON=" in example
    assert "PSKIT_MCP_CREDENTIAL_REFS_JSON=" in example
    assert "CORAL_MCP_BEARER_TOKEN=" in example
    rendered = _render(staging=True)
    receiver = rendered["services"]["mcp-receiver"]
    assert receiver["environment"]["PSKIT_MCP_CREDENTIAL_REFS_JSON"] == (
        '{"coral-mcp-token":"CORAL_MCP_BEARER_TOKEN"}'
    )
    assert "CORAL_MCP_BEARER_TOKEN" not in rendered["services"]["backend"]["environment"]
    assert not receiver.get("ports")
