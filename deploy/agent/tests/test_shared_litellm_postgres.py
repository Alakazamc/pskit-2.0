"""A candidate LiteLLM gateway shares only the Supabase PostgreSQL server."""

import json
import os
import stat
import subprocess
from pathlib import Path

import psycopg
import pytest

from deploy.agent.scripts.provision_shared_postgres import provision_shared_postgres
from infra.litellm import bootstrap_pskit

ROOT = Path(__file__).resolve().parents[3]
SHARED_COMPOSE = ROOT / "infra/litellm/compose.shared-postgres.yaml"


@pytest.fixture
def provisioned_pg():
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN to run PostgreSQL integration tests")
    provision_shared_postgres(
        dsn, litellm_password="local-litellm-test-password",
        pskit_password="local-pskit-test-password",
    )
    return dsn


def test_provision_creates_separate_database_and_roles(provisioned_pg):
    provision_shared_postgres(
        provisioned_pg, litellm_password="local-litellm-test-password",
        pskit_password="local-pskit-test-password",
    )
    with psycopg.connect(provisioned_pg) as admin:
        assert admin.execute("SELECT 1 FROM pg_database WHERE datname='litellm'").fetchone()
        assert admin.execute("SELECT 1 FROM pg_roles WHERE rolname='litellm'").fetchone()
        assert admin.execute("SELECT 1 FROM pg_roles WHERE rolname='pskit_app'").fetchone()
        assert admin.execute("SELECT 1 FROM pg_namespace WHERE nspname='pskit'").fetchone()
    litellm_dsn = psycopg.conninfo.make_conninfo(
        provisioned_pg, dbname="litellm", user="litellm",
        password="local-litellm-test-password",
    )
    with psycopg.connect(litellm_dsn) as gateway:
        assert gateway.execute("SELECT current_database(), current_user").fetchone() == (
            "litellm", "litellm",
        )
        gateway.execute("CREATE TABLE IF NOT EXISTS public.pskit_permission_probe (id integer)")
        gateway.execute("DROP TABLE public.pskit_permission_probe")


def test_app_role_cannot_read_auth(provisioned_pg):
    with psycopg.connect(provisioned_pg, autocommit=True) as admin:
        assert admin.execute(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='auth' AND c.relname='users'"
        ).fetchone()
    app_dsn = psycopg.conninfo.make_conninfo(
        provisioned_pg, dbname="postgres", user="pskit_app",
        password="local-pskit-test-password",
    )
    with psycopg.connect(app_dsn) as app:
        assert app.execute("SELECT current_user").fetchone() == ("pskit_app",)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("SELECT * FROM auth.users").fetchall()


def test_shared_compose_has_no_db_service():
    env = os.environ.copy()
    env.update({
        "LITELLM_DB_PASSWORD": "test-password",
        "LITELLM_MASTER_KEY": "sk-test-master",
        "LITELLM_SALT_KEY": "sk-test-salt",
        "LITELLM_BIND_IP": "10.9.8.1",
        "LITELLM_PUBLIC_PORT": "4001",
    })
    result = subprocess.run(
        ["docker", "compose", "-f", str(SHARED_COMPOSE), "config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    rendered = json.loads(result.stdout)
    assert set(rendered["services"]) == {"gateway"}
    gateway = rendered["services"]["gateway"]
    assert gateway["image"] == "ghcr.io/berriai/litellm:v1.100.3"
    assert gateway["environment"]["DATABASE_URL"].endswith("@db:5432/litellm")
    assert gateway["ports"][0]["host_ip"] == "10.9.8.1"
    assert gateway["ports"][0]["published"] == "4001"
    assert rendered["networks"]["supabase"]["name"] == "pskit-agent-supabase_default"
    assert rendered["networks"]["supabase"]["external"] is True


def test_bootstrap_never_reuses_old_key_file(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("LITELLM_MASTER_KEY=sk-test-master\n")
    old_key = tmp_path / ".pskit-virtual-key"
    old_key.write_text("sk-old-database-key\n")
    old_key.chmod(0o600)
    candidate_key = tmp_path / ".pskit-candidate-virtual-key"
    monkeypatch.setattr(bootstrap_pskit, "ROOT", tmp_path)
    calls = []

    def fake_request(path, *, payload=None):
        calls.append((path, payload))
        return {
            "/budget/info": [],
            "/budget/new": {"budget_id": "pskit-member-monthly"},
            "/team/info?team_id=pskit-lab": {"team_id": "pskit-lab", "max_budget": 10},
            "/key/generate": {"key": "sk-new-database-key"},
        }[path]

    monkeypatch.setattr(bootstrap_pskit, "_request", fake_request)
    bootstrap_pskit.main([
        "--base-url", "http://10.9.8.1:4001", "--key-file", str(candidate_key),
    ])
    assert old_key.read_text() == "sk-old-database-key\n"
    assert candidate_key.read_text() == "sk-new-database-key\n"
    assert stat.S_IMODE(candidate_key.stat().st_mode) == 0o600
    assert bootstrap_pskit.BASE_URL == "http://10.9.8.1:4001"
    assert ("/budget/new", {
        "budget_id": "pskit-member-monthly", "max_budget": 2,
        "budget_duration": "30d",
    }) in calls
    assert any(path == "/key/generate" for path, _ in calls)
    output = capsys.readouterr().out
    assert "sk-old-database-key" not in output
    assert "sk-new-database-key" not in output


def test_candidate_rejects_legacy_key_destination(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LITELLM_MASTER_KEY=sk-test-master\n")
    monkeypatch.setattr(bootstrap_pskit, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="candidate|legacy|old"):
        bootstrap_pskit.main([
            "--base-url", "http://10.9.8.1:4001",
            "--key-file", str(tmp_path / ".pskit-virtual-key"),
        ])


def test_existing_candidate_key_must_authenticate_against_target(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LITELLM_MASTER_KEY=sk-test-master\n")
    candidate = tmp_path / ".pskit-candidate-virtual-key"
    candidate.write_text("sk-other-database-key\n")
    candidate.chmod(0o600)
    monkeypatch.setattr(bootstrap_pskit, "ROOT", tmp_path)

    def reject(_key):
        raise RuntimeError("Saved virtual key is invalid for this gateway")

    monkeypatch.setattr(bootstrap_pskit, "_verify_existing_key", reject)
    with pytest.raises(RuntimeError, match="invalid for this gateway"):
        bootstrap_pskit.main([
            "--base-url", "http://10.9.8.1:4001", "--key-file", str(candidate),
        ])
