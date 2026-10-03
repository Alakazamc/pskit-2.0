"""The optional sandbox overlay keeps Docker control out of FastAPI."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_sandbox_overlay_exposes_only_private_manager(tmp_path):
    backend_env = tmp_path / "backend.env"
    backend_env.write_text("MODEL_GATEWAY_MODEL=test-model\n")
    proxy_env = tmp_path / "proxy.env"
    proxy_env.write_text("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=" + "x" * 40 + "\n")
    env = {**os.environ,
           "AGENT_BACKEND_ENV_FILE": str(backend_env),
           "AGENT_AF3_PROXY_KEY_FILE": str(proxy_env),
           "AGENT_SANDBOX_MANAGER_TOKEN": "manager-token-123456",
           "AGENT_SANDBOX_BRIDGE_SECRET": "bridge-secret-123456",
           "AGENT_SANDBOX_NETWORK": "pskit-agent-local_app",
           "AGENT_SANDBOX_NAMESPACE": "local",
           "AGENT_SANDBOX_IMAGE": "pskit-agent-backend:local"}
    result = subprocess.run([
        "docker", "compose", "-f", str(ROOT / "deploy/agent/compose.yaml"),
        "-f", str(ROOT / "deploy/agent/compose.local.yaml"),
        "-f", str(ROOT / "deploy/agent/compose.sandbox.yaml"),
        "config", "--format", "json",
    ], env=env, capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]

    assert services["backend"]["environment"]["RESEARCH_AGENT_PI_EXECUTION"] == "sandbox"
    assert services["backend"]["environment"]["RESEARCH_AGENT_INTERNAL_API_URL"] == (
        "http://backend:8000"
    )
    assert not any("docker.sock" in str(item) for item in services["backend"]["volumes"])
    manager = services["sandbox-manager"]
    assert set(manager["networks"]) == {"app"}
    assert manager.get("ports") is None
    assert manager["volumes"][0]["target"] == "/var/run/docker.sock"
    assert manager["environment"]["PSKIT_SANDBOX_NETWORK"] == "pskit-agent-local_app"
    assert manager["environment"]["PSKIT_SANDBOX_IMAGE"] == "pskit-agent-backend:local"
