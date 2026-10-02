"""Local Docker topology contract, checked from rendered Compose output."""

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/agent/compose.yaml"
LOCAL = ROOT / "deploy/agent/compose.local.yaml"


def test_local_compose_is_private_and_persistent(tmp_path):
    backend_env = tmp_path / "backend.env"
    backend_env.write_text("LOCAL_BACKEND_SECRET_TEST=backend-only\n")
    key_file = tmp_path / "callback.env"
    key_file.write_text("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=" + "x" * 40 + "\n")
    for path in (backend_env, key_file):
        path.chmod(0o600)
    env = {
        **os.environ,
        "AGENT_BACKEND_ENV_FILE": str(backend_env),
        "AGENT_AF3_PROXY_KEY_FILE": str(key_file),
        "SUPABASE_DOCKER_NETWORK": "pskit-supabase_default",
        "AGENT_PUBLIC_URL": "http://127.0.0.1:18085",
    }
    result = subprocess.run([
        "docker", "compose", "-f", str(COMPOSE), "-f", str(LOCAL),
        "config", "--format", "json",
    ], env=env, capture_output=True, text=True, check=True)
    config = json.loads(result.stdout)
    services = config["services"]
    assert set(services) == {"backend", "web", "af3-callback-proxy", "model-gateway-mock"}
    assert config["networks"]["app"]["internal"] is True
    assert config["networks"]["supabase"]["external"] is True
    assert config["networks"]["supabase"]["name"] == "pskit-supabase_default"
    assert set(services["af3-callback-proxy"]["networks"]) == {"app", "callback_ingress"}
    assert config["networks"]["callback_ingress"].get("internal", False) is False
    assert set(services["backend"]["networks"]) == {"app", "supabase"}
    assert set(services["web"]["networks"]) == {"app", "supabase"}
    assert services["backend"]["environment"]["LOCAL_BACKEND_SECRET_TEST"] == "backend-only"
    assert "LOCAL_BACKEND_SECRET_TEST" not in services["af3-callback-proxy"].get("environment", {})
    assert services["af3-callback-proxy"]["volumes"][0]["source"] == str(key_file)
    assert services["backend"]["volumes"][0]["target"] == "/data"
    for service, port in (("web", 18085), ("backend", 18088),
                          ("af3-callback-proxy", 18185)):
        assert services[service]["ports"] == [{
            "mode": "ingress", "target": services[service]["ports"][0]["target"],
            "published": str(port), "protocol": "tcp", "host_ip": "127.0.0.1",
        }]
    assert services["model-gateway-mock"]["image"].startswith(
        "python:3.12.12-slim-bookworm@sha256:"
    )


def test_model_stub_supports_suspend_and_resume(tmp_path):
    script = ROOT / "deploy/agent/tests/mock_model_gateway.py"
    import http.client
    import socket
    import time

    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen([
        "python", str(script), "--host", "127.0.0.1", "--port", str(port),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(40):
            if process.poll() is not None:
                raise AssertionError("model stub exited before accepting requests")
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.connect()
                connection.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("model stub did not start")

        for message, expected in (
            ("Say hello", "content"),
            ("Run AF3", "tool_calls"),
            ("Background task result: AF3 completed", "content"),
        ):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("POST", "/v1/chat/completions", body=json.dumps({
                "model": "local-stub", "stream": True,
                "messages": [
                    {"role": "system", "content": "AF3 is available when requested."},
                    {"role": "user", "content": message},
                ],
            }), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 200
            events = response.read().decode()
            assert '"' + expected + '"' in events
            assert "data: [DONE]" in events
            if expected == "tool_calls":
                chunks = [json.loads(line[6:]) for line in events.splitlines()
                          if line.startswith("data: {")]
                calls = [call for chunk in chunks for choice in chunk["choices"]
                         for call in choice["delta"].get("tool_calls", [])]
                arguments = json.loads(calls[0]["function"]["arguments"])
                assert arguments["fold_input"]["dialect"] == "alphafold3"
            connection.close()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_local_smoke_artifact_ids_are_unique_per_job():
    import runpy

    script = runpy.run_path(str(ROOT / "deploy/agent/tests/smoke_local.py"))
    artifact_id_for_job = script["artifact_id_for_job"]
    assert artifact_id_for_job("job-a") == artifact_id_for_job("job-a")
    assert artifact_id_for_job("job-a") != artifact_id_for_job("job-b")
