"""Staging the final backend configuration must not alter the live files."""

from pathlib import Path

import pytest

from deploy.agent.scripts.prepare_cloud_postgres_backend import prepare_backend


def _private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


def _root(tmp_path: Path) -> Path:
    agent = tmp_path / "deploy/agent"
    llm = tmp_path / "infra/litellm"
    _private(
        agent / ".env.stack-admin",
        "PSKIT_DB_PASSWORD=local+test/password\nLITELLM_DB_PASSWORD=other-test-password\n",
    )
    _private(llm / ".pskit-candidate-virtual-key", "sk-new-candidate\n")
    _private(
        agent / "cloud.backend.env",
        "RESEARCH_AGENT_MODE=live\n"
        "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=callback-test-key\n"
        "SUPABASE_PUBLISHABLE_KEY=test-public-key\n"
        "MODEL_GATEWAY_API_KEY=sk-old-key\n"
        "MODEL_GATEWAY_MODEL=claude-opus-4-8\n",
    )
    _private(agent / "cloud.proxy.env", "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=callback-test-key\n")
    _private(
        agent / ".env",
        "AGENT_BACKEND_IMAGE=pskit-agent-backend:old\n"
        "AGENT_WEB_IMAGE=pskit-agent-web:old\n"
        "AGENT_PUBLIC_URL=https://agent.bioailab.net\n",
    )
    return tmp_path


def test_prepares_isolated_private_backend_and_stack_files(tmp_path, capsys):
    root = _root(tmp_path)
    original = (root / "deploy/agent/cloud.backend.env").read_bytes()
    prepare_backend(root)
    agent = root / "deploy/agent"
    backend = agent / "cloud.backend.pg17.env"
    cloud = agent / "cloud.env"
    stack = agent / ".env.stack"
    for path in (backend, cloud, stack):
        assert path.stat().st_mode & 0o777 == 0o600
    assert "postgresql://pskit_app:local%2Btest%2Fpassword@db:5432/postgres" in backend.read_text()
    assert "MODEL_GATEWAY_API_KEY=sk-new-candidate" in backend.read_text()
    assert "MODEL_GATEWAY_API_KEY=sk-old-key" not in backend.read_text()
    assert "AGENT_PG_DATA_VOLUME=pskit-agent-cloud-pg17-20261003_agent_data" in cloud.read_text()
    assert "AGENT_BACKEND_IMAGE=pskit-agent-backend:pg17-20261003-r1" in cloud.read_text()
    assert "STACK_BACKEND_ENV_FILE=" in stack.read_text()
    assert (agent / "cloud.backend.env").read_bytes() == original
    assert "sk-new-candidate" not in capsys.readouterr().out


def test_refuses_missing_key_or_existing_target(tmp_path):
    root = _root(tmp_path)
    (root / "infra/litellm/.pskit-candidate-virtual-key").unlink()
    with pytest.raises(ValueError, match="missing"):
        prepare_backend(root)
    _private(root / "infra/litellm/.pskit-candidate-virtual-key", "sk-new-candidate\n")
    prepare_backend(root)
    with pytest.raises(FileExistsError):
        prepare_backend(root)
