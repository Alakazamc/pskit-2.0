"""Rendered cloud overlays must honor the backend's configured member GPU quota."""

from pathlib import Path

import pytest

from deploy.agent.tests.test_staging_compose import DEPLOY, _render


def render_backend(tmp_path: Path, overlays: list[str], minutes: int):
    backend_env = tmp_path / "backend.env"
    lines = (DEPLOY / "cloud.backend.env.example").read_text().splitlines()
    key = "RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES"
    lines = [line for line in lines if not line.startswith(key + "=")]
    backend_env.write_text("\n".join([*lines, f"{key}={minutes}"]) + "\n")
    rendered = _render("pskit-quota-test", DEPLOY / "cloud.env.example", [
        DEPLOY / "compose.yaml", DEPLOY / "compose.cloud.yaml",
        *(DEPLOY / overlay for overlay in overlays),
    ], {
        "AGENT_BACKEND_ENV_FILE": str(backend_env),
        "AGENT_AF3_PROXY_KEY_FILE": str(DEPLOY / "cloud.backend.env.example"),
        "AGENT_MCP_RECEIVER_ENV_FILE": str(DEPLOY / "mcp.receiver.env.example"),
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:test-fixed",
        "AGENT_PG_DATA_VOLUME": "pskit-quota-test_agent_data",
        "AGENT_MCP_RECEIVER_DATA_VOLUME": "pskit-quota-test_receiver_data",
        "SUPABASE_DOCKER_NETWORK": "pskit-agent-supabase-staging_default",
    })
    return rendered["services"]["backend"]["environment"]


@pytest.mark.parametrize("overlays", [
    [], ["compose.postgres.yaml"], ["compose.postgres.yaml", "compose.staging.yaml"],
])
@pytest.mark.parametrize("minutes", [60, 7, 0])
def test_cloud_overlays_preserve_configured_member_gpu_quota(tmp_path, overlays, minutes):
    environment = render_backend(tmp_path, overlays, minutes)
    assert environment["RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES"] == str(minutes)
    assert environment["RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES"] == "0"
