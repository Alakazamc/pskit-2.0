"""Candidate secrets must be fresh, private and kept off stdout."""

from pathlib import Path

import pytest

from deploy.agent.scripts.prepare_single_postgres_candidate import prepare_candidate


def _root(tmp_path: Path) -> Path:
    (tmp_path / "infra/supabase").mkdir(parents=True)
    (tmp_path / "infra/litellm").mkdir()
    (tmp_path / "deploy/agent").mkdir(parents=True)
    source = tmp_path / "infra/supabase/.env"
    source.write_text("POSTGRES_PASSWORD=local+test/password\n")
    source.chmod(0o600)
    return tmp_path


def test_candidate_uses_supabase_database_and_fresh_private_keys(tmp_path, capsys):
    root = _root(tmp_path)
    prepare_candidate(root)
    admin = root / "deploy/agent/.env.stack-admin"
    gateway = root / "infra/litellm/.env.shared"
    assert admin.stat().st_mode & 0o777 == 0o600
    assert gateway.stat().st_mode & 0o777 == 0o600
    admin_text = admin.read_text()
    gateway_text = gateway.read_text()
    assert "local%2Btest%2Fpassword@db:5432/postgres" in admin_text
    assert "RESEARCH_AGENT_DATABASE_URL=" in admin_text
    assert "LITELLM_PUBLIC_PORT=4001" in gateway_text
    assert "LITELLM_BIND_IP=10.9.8.1" in gateway_text
    assert "LITELLM_DB_PASSWORD=" in gateway_text
    assert "LITELLM_MASTER_KEY=sk-" in gateway_text
    assert "local+test/password" not in capsys.readouterr().out


def test_candidate_refuses_existing_files_and_unsafe_supabase_env(tmp_path):
    root = _root(tmp_path)
    prepare_candidate(root)
    with pytest.raises(FileExistsError):
        prepare_candidate(root)
    (root / "infra/supabase/.env").chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        prepare_candidate(root)
