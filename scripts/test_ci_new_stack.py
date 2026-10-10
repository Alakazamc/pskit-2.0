"""Regression checks for CI isolation, finite execution and PostgreSQL gates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import ci_new_stack
import pytest
from ci_new_stack import ROOT, isolated_env, require_postgres, run, snapshot
from ci_render_compose import fixture


def test_isolation_removes_live_config_and_caller_python_options(tmp_path, monkeypatch):
    for key in ("RESEARCH_AGENT_DATABASE_URL", "SUPABASE_SECRET_KEY", "MODEL_GATEWAY_API_KEY",
                "OPENAI_API_KEY", "TEST_SUPABASE_URL", "TEST_POSTGRES_DSN", "PYTEST_ADDOPTS",
                "COMPOSE_FILE", "NPM_CONFIG_USERCONFIG", "PYTHONPATH"):
        monkeypatch.setenv(key, "inherited-live-value")
    env = isolated_env(tmp_path, tmp_path / "source")
    assert "inherited-live-value" not in env.values()
    assert env["RESEARCH_AGENT_MODE"] == env["RESEARCH_AGENT_RUNTIME"] == "mock"
    assert env["VITE_AUTH_MODE"] == "demo"
    assert env["PYTHONPYCACHEPREFIX"].startswith(str(tmp_path))
    assert env["HOME"].startswith(str(tmp_path))


def test_snapshot_omits_private_files_datasets_and_dependencies(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    for name in ("apps/api/app/main.py", "apps/api/.env", "apps/api/.env.production",
                 "apps/api/local.env", "apps/api/.env.example", "apps/api/data/big.dat",
                 "apps/api/pi/node_modules/pkg/index.js", "deploy_to_cloud.sh"):
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("example")
    target = tmp_path / "copy"
    snapshot(target, repository)
    copied = {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()}
    assert copied == {"apps/api/app/main.py", "apps/api/.env.example", "deploy_to_cloud.sh"}


def test_snapshot_rejects_source_symlinks(tmp_path):
    repository = tmp_path / "repository"
    (repository / "apps/api").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "apps/api/external.py").symlink_to(ROOT / "apps/api/app/main.py")
    with pytest.raises(RuntimeError, match="unexpected source"):
        snapshot(tmp_path / "copy", repository)


def test_dependency_links_keep_generated_caches_in_disposable_tree(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    installed = root / "apps/web/node_modules"
    for name in ("vite", ".bin", ".vite", ".vite-temp"):
        (installed / name).mkdir(parents=True)
    monkeypatch.setattr(ci_new_stack, "ROOT", root)
    source = tmp_path / "source"
    ci_new_stack.link_dependencies(source, "apps/web")
    linked = source / "apps/web/node_modules"
    assert linked.is_dir() and not linked.is_symlink()
    assert (linked / "vite").resolve() == installed / "vite"
    assert (linked / ".bin").resolve() == installed / ".bin"
    for cache in (".vite", ".vite-temp"):
        assert not (linked / cache).exists()
        (linked / cache).mkdir()
        (linked / cache / "generated").write_text("temporary")
        assert not (installed / cache / "generated").exists()


def test_postgres_gate_requires_an_explicit_local_dsn():
    with pytest.raises(RuntimeError, match="required"):
        require_postgres("")
    with pytest.raises(RuntimeError, match="loopback"):
        require_postgres("postgresql://ci:ci@production.example.invalid/ci")
    with pytest.raises(RuntimeError, match="loopback"):
        require_postgres("host=localhost hostaddr=192.0.2.1 dbname=ci")
    with pytest.raises(RuntimeError, match="loopback"):
        require_postgres("host=localhost service=live")


def test_runner_stops_a_slow_process_and_preserves_output(tmp_path):
    log = tmp_path / "check.log"
    with pytest.raises(RuntimeError, match="timed out"):
        run([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
            tmp_path, dict(os.environ), 0.5, log=log)
    assert "started" in log.read_text()


def run_pg_guard(tmp_path: Path, source: str, *, dsn: bool = True) -> subprocess.CompletedProcess:
    test = tmp_path / "tests/postgres/test_guard.py"
    test.parent.mkdir(parents=True)
    test.write_text(source)
    env = isolated_env(tmp_path, ROOT)
    env.update(PSKIT_CI_REQUIRE_POSTGRES="1", PSKIT_CI_EXPECT_POSTGRES="1")
    if dsn:
        env["TEST_POSTGRES_DSN"] = "synthetic-nonempty-guard-only"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "ci_pg_guard", str(test),
         "-o", f"cache_dir={tmp_path / 'cache'}"], cwd=tmp_path, env=env, text=True,
        capture_output=True, timeout=30, check=False,
    )


def test_pg_guard_fails_before_collection_without_dsn(tmp_path):
    result = run_pg_guard(tmp_path, "def test_ok(): assert True\n", dsn=False)
    assert result.returncode != 0
    assert "TEST_POSTGRES_DSN is required" in result.stderr


def test_pg_guard_rejects_skipped_postgres_cases(tmp_path):
    result = run_pg_guard(tmp_path, "import pytest\ndef test_pg(): pytest.skip('fixture skipped')\n")
    assert result.returncode == 1
    assert "CI forbids skipped PostgreSQL cases" in result.stdout


def test_pg_guard_reports_executed_cases(tmp_path):
    result = run_pg_guard(tmp_path, "def test_pg(): assert True\n")
    assert result.returncode == 0, result.stderr
    assert "PostgreSQL gate: 1 passed cases; 0 skipped" in result.stdout


def test_compose_fixture_uses_only_temporary_synthetic_private_paths(tmp_path):
    stage = fixture(tmp_path, "staging")
    production = fixture(tmp_path, "production")
    for path in (stage, production):
        values = dict(line.split("=", 1) for line in path.read_text().splitlines())
        for name in ("AGENT_BACKEND_ENV_FILE", "AGENT_AF3_PROXY_KEY_FILE",
                     "AGENT_MCP_RECEIVER_ENV_FILE", "OPENSANDBOX_CONFIG_FILE",
                     "OPENSANDBOX_ROLLOUT_DIR"):
            assert Path(values[name]).is_relative_to(tmp_path)
        assert values["OPENAI_API_KEY"] == ""
        assert values["COMPOSE_FILE"] == ""
        assert "ci-" in values["POSTGRES_PASSWORD"]
    assert stage.read_text() != production.read_text()
