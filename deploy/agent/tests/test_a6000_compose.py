"""A6000 deployment contract: one private backend, no public Docker ports."""

from pathlib import Path
import json
import os
import subprocess


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/agent"


def rendered_stack() -> dict:
    environment = os.environ.copy()
    example_backend = str(DEPLOY / "a6000.backend.env.example")
    environment["AGENT_BACKEND_ENV_FILE"] = example_backend
    environment["AGENT_AF3_PROXY_KEY_FILE"] = example_backend
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(DEPLOY / "a6000.env.example"),
            "-f", str(DEPLOY / "compose.a6000.yaml"), "--profile", "private-test",
            "config", "--format", "json",
        ],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_a6000_stack_has_one_loopback_backend_and_no_web():
    assert (DEPLOY / "compose.a6000.yaml").exists()
    env_example = (DEPLOY / "a6000.env.example").read_text()
    assert "AGENT_BACKEND_ENV_FILE=./a6000.backend.env\n" in env_example
    assert "AGENT_AF3_PROXY_KEY_FILE=./a6000.backend.env\n" in env_example
    assert "${AGENT_API_PROXY_IMAGE:-nginx:1.28.0-alpine@sha256:" in (
        DEPLOY / "compose.a6000.yaml"
    ).read_text()
    stack = rendered_stack()
    assert stack["name"] == "pskit-agent-a6000"
    services = stack["services"]
    assert set(services) == {
        "backend", "api-proxy", "af3-callback-proxy", "model-gateway-mock",
    }
    assert services["backend"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["backend"]["ports"][0]["published"] == "18089"
    assert services["af3-callback-proxy"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["af3-callback-proxy"]["ports"][0]["published"] == "18185"
    assert services["af3-callback-proxy"]["user"] == "1006:1006"
    assert services["backend"]["environment"]["SUPABASE_URL"] == "http://10.9.8.1:18130"
    assert services["backend"]["environment"]["SUPABASE_PUBLIC_URL"] == (
        "https://agent.bioailab.net"
    )
    assert services["backend"]["environment"]["RESEARCH_AGENT_PUBLIC_API_URL"] == (
        "https://agent.bioailab.net"
    )
    assert services["backend"]["environment"]["RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES"] == "0"
    assert services["backend"]["environment"]["RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES"] == "0"
    assert services["model-gateway-mock"]["profiles"] == ["private-test"]
    assert services["api-proxy"]["network_mode"] == "host"
    assert services["api-proxy"]["user"] == "101:101"
    assert services["api-proxy"]["cap_drop"] == ["ALL"]
    assert services["api-proxy"]["image"].startswith("nginx:1.28.0-alpine@sha256:")
    assert not services["api-proxy"].get("ports")
    assert not any("latest" in service.get("image", "") for service in services.values())
    assert not stack["networks"]["app"].get("internal", False)


def test_api_proxy_allows_only_cloud_and_public_api():
    path = DEPLOY / "api-a6000.conf"
    assert path.exists()
    config = path.read_text()
    assert "listen 10.9.8.2:18088;" in config
    assert "allow 10.9.8.1;" in config
    assert "deny all;" in config
    assert "location = /internal { return 404; }" in config
    assert "location ^~ /internal/ { return 404; }" in config
    assert "location ^~ /api/v1/ {" in config
    assert "proxy_pass http://127.0.0.1:18089;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_request_buffering off;" in config
    assert "listen 0.0.0.0" not in config
