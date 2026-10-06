from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.agent.scripts.enable_staging_mcp_receiver import enable_staging_mcp_receiver


def _private(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    root = tmp_path / "staging-private"
    root.mkdir(mode=0o700)
    _private(root / "cloud.env", "\n".join([
        "AGENT_BACKEND_IMAGE=pskit-agent-backend:fixed",
        "AGENT_WEB_IMAGE=pskit-agent-web:unused-staging",
        f"AGENT_BACKEND_ENV_FILE={root / 'backend.env'}",
        f"AGENT_AF3_PROXY_KEY_FILE={root / 'proxy.env'}",
        "AGENT_PUBLIC_URL=http://10.9.8.1:18132",
        "TURNSTILE_SITE_KEY=1x00000000000000000000AA",
        "AGENT_PG_DATA_VOLUME=pskit-agent-staging_agent_data",
        "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase-staging_default",
    ]) + "\n")
    _private(root / "backend.env.base", "RESEARCH_AGENT_MODE=live\n")
    _private(root / "backend.env", "RESEARCH_AGENT_MODE=live\nMODEL_GATEWAY_API_KEY=sk-stage\n")
    return root


def _env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_upgrades_existing_staging_without_replacing_other_secrets(staging: Path):
    backup = enable_staging_mcp_receiver(staging)
    receiver = _env(staging / "mcp.receiver.env")
    backend_base = _env(staging / "backend.env.base")
    backend = _env(staging / "backend.env")
    cloud = _env(staging / "cloud.env")

    assert backup is not None and backup.is_dir()
    assert (staging / "mcp.receiver.env").stat().st_mode & 0o777 == 0o600
    assert backend["MODEL_GATEWAY_API_KEY"] == "sk-stage"
    expected = {"coral-mcp": receiver["PSKIT_COMPUTE_SERVICE_KEY"]}
    assert json.loads(backend_base["RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON"]) == expected
    assert json.loads(backend_base["RESEARCH_AGENT_ADMIN_MCP_NETWORK_ZONES_JSON"]) == {
        "wireguard-private": ["172.31.226.126/32"]
    }
    assert backend_base["RESEARCH_AGENT_MCP_TIMEOUT_SECONDS"] == "900"
    assert json.loads(backend["RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON"]) == expected
    assert (staging / "backend.env").read_text() == (
        (staging / "backend.env.base").read_text().rstrip("\n")
        + "\nMODEL_GATEWAY_API_KEY=sk-stage\n"
    )
    assert cloud["AGENT_MCP_RECEIVER_ENV_FILE"] == str(staging / "mcp.receiver.env")
    assert cloud["AGENT_MCP_RECEIVER_DATA_VOLUME"] == "pskit-agent-staging_mcp_receiver_data"
    assert cloud["AGENT_MCP_SERVICE_ID"] == "coral-mcp"
    assert cloud["AGENT_MCP_WORKER_ID"] == "coral-mcp-staging-1"


def test_upgrade_is_idempotent_and_does_not_print_secret(staging: Path, capsys):
    enable_staging_mcp_receiver(staging)
    key = _env(staging / "mcp.receiver.env")["PSKIT_COMPUTE_SERVICE_KEY"]
    assert enable_staging_mcp_receiver(staging) is None
    assert key not in "".join(capsys.readouterr())


def test_upgrade_repairs_equivalent_backend_key_order(staging: Path):
    key = "receiver-key-at-least-thirty-two-bytes"
    mapping = json.dumps({"coral-mcp": key}, separators=(",", ":"))
    _private(
        staging / "backend.env.base",
        f"RESEARCH_AGENT_MODE=live\nRESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON={mapping}\n",
    )
    _private(
        staging / "backend.env",
        "RESEARCH_AGENT_MODE=live\nMODEL_GATEWAY_API_KEY=sk-stage\n"
        f"RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON={mapping}\n",
    )
    _private(
        staging / "mcp.receiver.env",
        f"PSKIT_COMPUTE_SERVICE_KEY={key}\nPSKIT_MCP_ENDPOINT_OVERRIDES_JSON={{}}\n"
        "PSKIT_MCP_CREDENTIAL_REFS_JSON={}\n",
    )

    enable_staging_mcp_receiver(staging)

    assert (staging / "backend.env").read_text() == (
        (staging / "backend.env.base").read_text().rstrip("\n")
        + "\nMODEL_GATEWAY_API_KEY=sk-stage\n"
    )


def test_upgrade_rejects_unsafe_or_conflicting_private_state(staging: Path):
    staging.chmod(0o755)
    with pytest.raises(ValueError, match="unsafe"):
        enable_staging_mcp_receiver(staging)
    staging.chmod(0o700)
    _private(staging / "mcp.receiver.env", "PSKIT_COMPUTE_SERVICE_KEY=receiver-key-at-least-thirty-two-bytes\n"
             "PSKIT_MCP_ENDPOINT_OVERRIDES_JSON={}\nPSKIT_MCP_CREDENTIAL_REFS_JSON={}\n")
    with (staging / "backend.env.base").open("a", encoding="utf-8") as stream:
        stream.write('RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON={"coral-mcp":"different-key-at-least-thirty-two-bytes"}\n')
    with pytest.raises(ValueError, match="conflicts"):
        enable_staging_mcp_receiver(staging)
