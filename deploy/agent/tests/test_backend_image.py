"""Runtime contract for the locally built Python and Pi image."""

import json
import os
import subprocess


IMAGE = os.getenv("PSKIT_BACKEND_IMAGE", "pskit-agent-backend:local")


def docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_backend_image_runtime_and_storage():
    metadata = json.loads(docker("image", "inspect", IMAGE))[0]
    assert metadata["Config"]["User"] not in {"", "0", "root"}
    output = docker(
        "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
        "id -u; python -c 'import sys; print(sys.version_info.major, sys.version_info.minor)'; "
        "node --version; pi --version; touch /data/image-contract-check",
    )
    lines = output.splitlines()
    assert int(lines[0]) > 0
    assert lines[1] == "3 12"
    assert lines[2].startswith("v22.")
    assert "0.87.1" in lines[3]


def test_backend_image_excludes_secrets():
    docker("image", "inspect", IMAGE)
    files = docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
                   "find /app -name '.env*' -o -name 'agent.sqlite3'")
    assert files == ""
    history = docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", IMAGE)
    assert "SUPABASE_SECRET_KEY=" not in history
    assert "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=" not in history
