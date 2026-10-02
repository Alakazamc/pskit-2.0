"""The local AF3 receiver reuses the existing spool and compute container."""

from pathlib import Path
import json
import os
import subprocess


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy/agent/compose.a6000-receiver.yaml"


def compose(*arguments: str, receiver_env: Path | None = None) -> str:
    environment = os.environ.copy()
    if receiver_env is not None:
        environment["AGENT_AF3_RECEIVER_ENV_FILE"] = str(receiver_env)
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *arguments],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=True,
    )
    return result.stdout


def test_receiver_preserves_spool_and_is_not_started_by_default(tmp_path: Path):
    assert COMPOSE.exists()
    assert "/data/jhli/pskit-af3-receiver-test-20261002/receiver.cloud.env" in COMPOSE.read_text()
    assert "af3-receiver" not in compose("config", "--services").splitlines()
    receiver_env = tmp_path / "receiver.env"
    receiver_env.write_text("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=test-only\n")
    stack = json.loads(compose("--profile", "cutover", "config",
                               "--no-env-resolution", "--format", "json",
                               receiver_env=receiver_env))
    assert stack["name"] == "pskit-agent-a6000-receiver"
    assert set(stack["services"]) == {"af3-receiver"}
    receiver = stack["services"]["af3-receiver"]
    assert receiver["image"] == "af3_mar5_jhli_2026_0923:v1"
    assert receiver["profiles"] == ["cutover"]
    assert receiver["network_mode"] == "host"
    assert receiver["user"] == "1006:1006"
    assert receiver["entrypoint"] == ["python3"]
    assert receiver["command"][:4] == [
        "/opt/af3_receiver.py", "receive", "--api-url", "http://127.0.0.1:18185",
    ]
    assert receiver["command"][receiver["command"].index("--worker-id") + 1] == (
        "a6000-af3-cloud-1"
    )
    assert receiver["command"][receiver["command"].index("--gpu-memory-mb") + 1] == (
        "49140"
    )
    mounts = {mount["target"]: mount["source"] for mount in receiver["volumes"]}
    assert mounts["/var/lib/af3-receiver"] == (
        "/data/jhli/pskit-af3-receiver-test-20261002/spool"
    )
    assert mounts["/opt/af3_receiver.py"] == (
        "/data/jhli/pskit-af3-receiver-test-20261002/af3_receiver.py"
    )
    assert "env_file:" in COMPOSE.read_text()
