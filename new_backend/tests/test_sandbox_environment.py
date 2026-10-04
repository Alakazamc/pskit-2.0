"""Pi receives scoped credentials while host and bridge secrets remain outside child env."""

import pytest

from app.contracts.sandbox import SandboxPromptRequest
from app.services.sandbox_sessions import SandboxSessionCoordinator


@pytest.mark.asyncio
async def test_pi_environment_contains_only_scoped_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("PSKIT_SANDBOX_BRIDGE_TOKEN", "bridge-global-secret")
    monkeypatch.setenv("RESEARCH_AGENT_INTERNAL_API_URL", "http://sandbox-gateway:8080")
    observed = []

    class Pi:
        async def prompt(self, session, message, event, **kwargs):
            observed.append(kwargs)
            return {"session_file": str(tmp_path / session / ".pi/turn"), "text": "ok"}

    coordinator = SandboxSessionCoordinator(tmp_path, lambda _: Pi())
    await coordinator.prompt(
        SandboxPromptRequest(
            user_id="alice",
            session_id="one",
            attempt_id="a",
            message="hi",
            model="m",
            environment={
                "PSKIT_USER_ID": "alice",
                "PSKIT_RUN_ID": "run",
                "PSKIT_AGENT_TOOL_TOKEN": "scoped-token",
                "MODEL_GATEWAY_API_KEY": "run.scoped-token",
                "OPENAI_API_KEY": "global-provider",
                "PSKIT_SANDBOX_MANAGER_TOKEN": "manager-secret",
                "PSKIT_INTERNAL_API_URL": "http://api-db:5432",
            },
        ),
        lambda _: None,
    )
    env = observed[0]["environment"]
    assert env["MODEL_GATEWAY_API_KEY"] == "run.scoped-token"
    assert env["PSKIT_INTERNAL_API_URL"] == "http://sandbox-gateway:8080"
    assert (
        not {"OPENAI_API_KEY", "PSKIT_SANDBOX_MANAGER_TOKEN", "PSKIT_SANDBOX_BRIDGE_TOKEN"}
        & env.keys()
    )
    assert observed[0]["isolated_environment"] is True


@pytest.mark.asyncio
async def test_pi_rejects_global_provider_credential(tmp_path):
    class Pi:
        async def prompt(self, *args, **kwargs):
            raise AssertionError("Pi must not receive a global provider key")

    coordinator = SandboxSessionCoordinator(tmp_path, lambda _: Pi())
    with pytest.raises(ValueError, match="scoped"):
        await coordinator.prompt(
            SandboxPromptRequest(
                user_id="alice",
                session_id="one",
                attempt_id="a",
                message="hi",
                model="m",
                environment={"PSKIT_RUN_ID": "run", "MODEL_GATEWAY_API_KEY": "master-key"},
            ),
            lambda _: None,
        )


def test_smoke_refuses_production_project_before_docker_access():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "deploy/agent/scripts/sandbox_smoke.py"),
            "--project",
            "pskit-agent-cloud",
            "--image",
            "invalid",
            "--gateway-image",
            "invalid",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Only fresh pskit-sandbox-test-* projects" in result.stderr
