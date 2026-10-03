"""The single-PostgreSQL cutover must fail closed and preserve rollback data."""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from deploy.agent.scripts.check_single_postgres_quiescence import check_quiescence
from infra.litellm import bootstrap_pskit

ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = ROOT / "deploy/agent/SINGLE_POSTGRES_CUTOVER.md"


@pytest.fixture
def sqlite_sources(tmp_path):
    agent = tmp_path / "agent.sqlite3"
    journal = tmp_path / "journal.sqlite3"
    with sqlite3.connect(agent) as db:
        db.execute("CREATE TABLE agent_runs (id text, status text)")
        db.execute("CREATE TABLE agent_jobs (id text, status text)")
        db.execute("INSERT INTO agent_runs VALUES ('run-done', 'completed')")
        db.execute("INSERT INTO agent_jobs VALUES ('job-done', 'completed')")
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TABLE jobs (id text)")
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    return agent, journal, jobs_dir


def test_runbook_blocks_cutover_with_active_job_or_unacked_journal(sqlite_sources):
    agent, journal, jobs_dir = sqlite_sources
    assert check_quiescence(agent, journal, jobs_dir) == (0, 0, 0, 0)
    with sqlite3.connect(agent) as db:
        db.execute("INSERT INTO agent_jobs VALUES ('job-running', 'running')")
    with pytest.raises(RuntimeError, match="active|unfinished"):
        check_quiescence(agent, journal, jobs_dir)
    with sqlite3.connect(agent) as db:
        db.execute("DELETE FROM agent_jobs WHERE id='job-running'")
    with sqlite3.connect(journal) as db:
        db.execute("INSERT INTO jobs VALUES ('job-unacked')")
    with pytest.raises(RuntimeError, match="journal|unacked"):
        check_quiescence(agent, journal, jobs_dir)
    text = RUNBOOK.read_text()
    assert "check_single_postgres_quiescence.py" in text
    assert "owned-jobs" in text and "spool" in text
    assert "A6000 停写" in text


def test_missing_receiver_journal_fails_closed(sqlite_sources):
    agent, journal, jobs_dir = sqlite_sources
    journal.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        check_quiescence(agent, journal, jobs_dir)


def test_unknown_or_null_run_status_blocks_cutover(sqlite_sources):
    agent, journal, jobs_dir = sqlite_sources
    with sqlite3.connect(agent) as db:
        db.execute("INSERT INTO agent_runs VALUES ('run-unknown', NULL)")
    with pytest.raises(RuntimeError, match="active"):
        check_quiescence(agent, journal, jobs_dir)
    with sqlite3.connect(agent) as db:
        db.execute("DELETE FROM agent_runs WHERE id='run-unknown'")
        db.execute("INSERT INTO agent_jobs VALUES ('job-unknown', NULL)")
    with pytest.raises(RuntimeError, match="active"):
        check_quiescence(agent, journal, jobs_dir)


def test_orphaned_spool_directory_blocks_cutover(sqlite_sources):
    agent, journal, jobs_dir = sqlite_sources
    (jobs_dir / "old-job").mkdir()
    with pytest.raises(RuntimeError, match="spool"):
        check_quiescence(agent, journal, jobs_dir)


def test_runbook_requires_reverse_export_after_new_write():
    text = RUNBOOK.read_text()
    rollback = text.split("## 已产生新写入后的回退", maxsplit=1)[1]
    assert rollback.index("冻结") < rollback.index("agent_data_migrate.py export")
    assert rollback.index("agent_data_migrate.py export") < rollback.index("启动旧后端")
    assert "新卷" in rollback and "Pi transcript" in rollback
    assert "journal" in rollback and "spool" in rollback
    assert "不要执行 `down -v`" in text


def test_runbook_keeps_old_litellm_until_new_key_works():
    text = RUNBOOK.read_text()
    assert text.index("候选 LiteLLM 4001") < text.index("旧 LiteLLM 4000 停止")
    assert text.index("新虚拟 key") < text.index("旧 LiteLLM 4000 停止")
    assert "10 美元" in text and "2 美元" in text
    assert "claude-opus-4-8" in text
    assert "pg_dumpall --globals-only" in text
    assert "pg_dump -Fc -d postgres" in text
    assert "pg_dump -Fc -d litellm" in text
    assert "install_host_nginx_agent_cloud.sh" in text
    assert "enable_private_af3_ingress.sh" in text
    assert "AGENT_AF3_API_URL=http://10.9.8.1:18184" in text
    assert "pskit.bioailab.net" in text and "/internal/" in text


def test_candidate_bootstrap_uses_new_private_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LITELLM_MASTER_KEY=sk-old-master\n")
    candidate_env = tmp_path / ".env.shared"
    candidate_env.write_text("LITELLM_MASTER_KEY=sk-new-master\n")
    candidate_env.chmod(0o600)
    monkeypatch.setattr(bootstrap_pskit, "ROOT", tmp_path)

    def fake_request(path, *, payload=None):
        return {
            "/budget/info": [],
            "/budget/new": {"budget_id": "pskit-member-monthly"},
            "/team/info?team_id=pskit-lab": {"team_id": "pskit-lab", "max_budget": 10},
            "/key/generate": {"key": "sk-new-backend-key"},
        }[path]

    monkeypatch.setattr(bootstrap_pskit, "_request", fake_request)
    bootstrap_pskit.main([
        "--base-url", "http://127.0.0.1:4001", "--env-file", str(candidate_env),
        "--key-file", str(tmp_path / ".pskit-candidate-virtual-key"),
    ])
    assert bootstrap_pskit._master_key() == "sk-new-master"


def test_final_backend_uses_a_fresh_agent_volume():
    deploy = ROOT / "deploy/agent"
    env = {
        "PATH": os.environ["PATH"],
        "AGENT_BACKEND_IMAGE": "pskit-agent-backend:test-fixed",
        "AGENT_WEB_IMAGE": "pskit-agent-web:unused-fixed",
        "AGENT_BACKEND_ENV_FILE": str(deploy / "cloud.backend.env.example"),
        "AGENT_AF3_PROXY_KEY_FILE": str(deploy / "cloud.backend.env.example"),
    }
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", str(deploy / "cloud.env.example"),
         "-f", str(deploy / "compose.yaml"),
         "-f", str(deploy / "compose.cloud.yaml"),
         "-f", str(deploy / "compose.postgres.yaml"), "config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    volume = json.loads(rendered.stdout)["volumes"]["agent_data"]["name"]
    assert volume == "pskit-agent-cloud-pg17-20261003_agent_data"
