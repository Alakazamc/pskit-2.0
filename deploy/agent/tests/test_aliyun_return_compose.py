"""The returned cloud backend must use a fresh Agent volume and loopback ports."""

from pathlib import Path
import json
import os
import subprocess


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/agent"
RETURN = DEPLOY / "compose.cloud-return.yaml"


def test_return_compose_uses_fresh_volume_and_loopback_ports():
    environment = os.environ.copy()
    environment["AGENT_BACKEND_ENV_FILE"] = str(DEPLOY / "cloud.backend.env.example")
    environment["AGENT_AF3_PROXY_KEY_FILE"] = str(DEPLOY / "cloud.backend.env.example")
    environment["AGENT_BACKEND_IMAGE"] = "pskit-agent-backend:test-20261003"
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(DEPLOY / "cloud.env.example"),
         "-f", str(DEPLOY / "compose.yaml"),
         "-f", str(DEPLOY / "compose.cloud.yaml"),
         "-f", str(RETURN), "config", "--format", "json"],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=True,
    )
    stack = json.loads(result.stdout)
    assert stack["name"] == "pskit-agent-cloud"
    assert stack["volumes"]["agent_data"]["name"] == (
        "pskit-agent-cloud-return-20261003_agent_data"
    )
    backend = stack["services"]["backend"]
    callback = stack["services"]["af3-callback-proxy"]
    assert backend["image"] == "pskit-agent-backend:test-20261003"
    assert callback["image"] == backend["image"]
    assert backend["volumes"] == [{
        "type": "volume", "source": "agent_data", "target": "/data",
        "volume": {},
    }]
    for service in stack["services"].values():
        assert all(mount.get("source") != "pskit-agent-cloud_agent_data"
                   for mount in service.get("volumes", []))
    assert backend["ports"] == [{
        "mode": "ingress", "target": 8000, "published": "18088",
        "protocol": "tcp", "host_ip": "127.0.0.1",
    }]
    assert callback["ports"] == [{
        "mode": "ingress", "target": 8080, "published": "18185",
        "protocol": "tcp", "host_ip": "127.0.0.1",
    }]
    assert backend["environment"]["SUPABASE_URL"] == "http://api-gw:8000"
    assert stack["networks"]["supabase"]["name"] == "pskit-agent-supabase_default"
