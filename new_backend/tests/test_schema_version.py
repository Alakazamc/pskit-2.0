import sqlite3

import pytest

from app.db.migrations import SCHEMA_VERSION
from app.domain.persistent_conversation import PersistentConversationStore
from scripts.agent_data_migrate import _sqlite_copy, _upgrade_sqlite_copy


def test_offline_import_upgrades_only_disposable_copy(tmp_path):
    """Legacy schema migration must never rewrite the source snapshot."""
    source = tmp_path / "source.sqlite3"
    store = PersistentConversationStore(str(source))
    store.db.close()
    with sqlite3.connect(source) as db:
        db.execute("DELETE FROM core_schema_migrations WHERE version=14")
        db.execute("PRAGMA user_version=13")
    disposable = tmp_path / "working.sqlite3"
    _sqlite_copy(source, disposable)
    _upgrade_sqlite_copy(disposable)
    with sqlite3.connect(source) as old, sqlite3.connect(disposable) as upgraded:
        assert old.execute("PRAGMA user_version").fetchone() == (13,)
        assert upgraded.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_version_thirteen_projects_get_default_icon_without_losing_data(tmp_path):
    path = tmp_path / "project-icons.sqlite3"
    store = PersistentConversationStore(str(path))
    project = store.create_project("alice", "旧项目", "旧描述")
    store.db.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE workspace_projects DROP COLUMN icon")
        db.execute("DELETE FROM core_schema_migrations WHERE version=14")
        db.execute("PRAGMA user_version=13")
    upgraded = PersistentConversationStore(str(path))
    assert next(item for item in upgraded.projects_for("alice") if item.id == project.id).icon == "folder"
    assert next(item for item in upgraded.projects_for("alice") if item.id == project.id).description == "旧描述"


def test_legacy_unversioned_database_is_upgraded_to_current_schema(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_messages (id TEXT PRIMARY KEY, user_id TEXT, "
                   "session_id TEXT, role TEXT, content TEXT, created_at TEXT)")
        db.execute("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, user_id TEXT, "
                   "session_id TEXT, status TEXT, created_at TEXT, "
                   "resume_attempts INTEGER DEFAULT 0, retry_after TEXT, "
                   "context_json TEXT DEFAULT '{}', lease_owner TEXT, lease_expires_at TEXT)")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute("SELECT version FROM core_schema_migrations ORDER BY version").fetchall() == [
        (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,),
    ]
    message_columns = {row[1] for row in store.db.execute("PRAGMA table_info(agent_messages)")}
    run_columns = {row[1] for row in store.db.execute("PRAGMA table_info(agent_runs)")}
    assert "parts_json" in message_columns
    assert {"context_json", "lease_owner", "lease_expires_at", "initial_attempts",
            "checkpoint_file", "last_resumed_job_id"} <= run_columns
    job_columns = {row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")}
    assert {"input_json", "worker_id", "lease_expires_at", "attempts", "lease_token", "simulation",
            "first_claimed_at", "gpu_accounting_status"} <= job_columns


def test_future_database_schema_is_rejected_before_writes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")

    with pytest.raises(ValueError, match="newer than this application"):
        PersistentConversationStore(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_version_one_database_adds_af3_input_columns(tmp_path):
    path = tmp_path / "version-one.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT)")
        db.execute("CREATE TABLE agent_approvals (id TEXT PRIMARY KEY, user_id TEXT, "
                   "run_id TEXT, tool_call_id TEXT, estimated_minutes INTEGER, status TEXT, "
                   "job_id TEXT, created_at TEXT)")
        db.execute("PRAGMA user_version=1")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "input_json" in {row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")}
    assert "input_json" in {row[1] for row in store.db.execute("PRAGMA table_info(agent_approvals)")}


def test_version_two_database_adds_compute_lease_columns(tmp_path):
    path = tmp_path / "version-two.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT, input_json TEXT)")
        db.execute("PRAGMA user_version=2")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    job_columns = {row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")}
    assert {"worker_id", "lease_expires_at", "attempts"} <= job_columns


def test_version_three_database_adds_compute_lease_token(tmp_path):
    path = tmp_path / "version-three.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT, input_json TEXT, "
                   "worker_id TEXT, lease_expires_at TEXT, attempts INTEGER DEFAULT 0)")
        db.execute("PRAGMA user_version=3")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "lease_token" in {row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")}


def test_version_four_database_adds_simulation_provenance(tmp_path):
    path = tmp_path / "version-four.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT, input_json TEXT, "
                   "worker_id TEXT, lease_expires_at TEXT, attempts INTEGER DEFAULT 0, "
                   "lease_token TEXT)")
        db.execute("PRAGMA user_version=4")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "simulation" in {row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")}
    assert store.db.execute("SELECT version FROM core_schema_migrations").fetchall() == [
        (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,),
    ]


def test_version_five_running_job_gets_a_recoverable_execution_start(tmp_path):
    path = tmp_path / "version-five.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT, input_json TEXT, "
                   "worker_id TEXT, lease_expires_at TEXT, attempts INTEGER DEFAULT 0, "
                   "lease_token TEXT, simulation INTEGER DEFAULT 0)")
        db.execute("INSERT INTO agent_jobs VALUES ('job-1','alice',NULL,NULL,'running',0,20,"
                   "NULL,'[]','2026-10-01T08:00:00+00:00','{}','a6000',NULL,1,NULL,0)")
        db.execute("PRAGMA user_version=5")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute(
        "SELECT first_claimed_at FROM agent_jobs WHERE id='job-1'"
    ).fetchone() == ("2026-10-01T08:00:00+00:00",)


def test_version_six_waiting_run_keeps_its_committed_pi_checkpoint(tmp_path):
    path = tmp_path / "version-six.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, user_id TEXT, "
                   "session_id TEXT, status TEXT, created_at TEXT)")
        db.execute("CREATE TABLE pi_sessions (session_id TEXT PRIMARY KEY, user_id TEXT, "
                   "session_file TEXT)")
        db.execute("INSERT INTO agent_runs(id,user_id,session_id,status,created_at) "
                   "VALUES ('run-1','alice','session-1','waiting','now')")
        db.execute("INSERT INTO pi_sessions VALUES ('session-1','alice','/tmp/checkpoint.jsonl')")
        db.execute("PRAGMA user_version=6")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute(
        "SELECT checkpoint_file FROM agent_runs WHERE id='run-1'",
    ).fetchone() == ("/tmp/checkpoint.jsonl",)


def test_version_seven_token_entries_gain_attempt_status_without_changing_amounts(tmp_path):
    path = tmp_path / "version-seven.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_token_entries (id INTEGER PRIMARY KEY, user_id TEXT, "
                   "period TEXT, kind TEXT, amount INTEGER, run_id TEXT, created_at TEXT)")
        db.execute("INSERT INTO agent_token_entries VALUES "
                   "(1,'alice','2026-10','reservation',8,'run-1','2026-10-01T00:00:00+00:00')")
        db.execute("PRAGMA user_version=7")

    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.db.execute("SELECT kind,amount,status FROM agent_token_entries").fetchall() == [
        ("reservation", 8, "posted"),
    ]


def test_failed_core_migration_is_atomic_and_can_retry(tmp_path, monkeypatch):
    from app.db import migrations

    path = tmp_path / "interrupted.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, user_id TEXT, run_id TEXT, "
                   "tool_call_id TEXT, status TEXT, progress INTEGER, estimated_minutes INTEGER, "
                   "actual_minutes INTEGER, artifacts TEXT, created_at TEXT, input_json TEXT, "
                   "worker_id TEXT, lease_expires_at TEXT, attempts INTEGER DEFAULT 0)")
        db.execute("INSERT INTO agent_jobs VALUES ('job-1','alice',NULL,NULL,'queued',0,5,NULL,'[]',"
                   "'2026-10-01T00:00:00+00:00',NULL,NULL,NULL,0)")
        db.execute("PRAGMA user_version=3")

    original = migrations.add_column_if_missing

    def interrupted(db, table, name, declaration):
        if name == "simulation":
            raise RuntimeError("simulated migration interruption")
        return original(db, table, name, declaration)

    monkeypatch.setattr(migrations, "add_column_if_missing", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        PersistentConversationStore(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "lease_token" not in {row[1] for row in db.execute("PRAGMA table_info(agent_jobs)")}
        assert db.execute("SELECT id FROM agent_jobs").fetchall() == [("job-1",)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='agent_events'").fetchall() == []

    monkeypatch.setattr(migrations, "add_column_if_missing", original)
    store = PersistentConversationStore(str(path))
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert {"lease_token", "simulation", "first_claimed_at"} <= {
        row[1] for row in store.db.execute("PRAGMA table_info(agent_jobs)")
    }


def test_version_eight_database_adds_public_af3_request_keys(tmp_path):
    path = tmp_path / "existing-v8.sqlite3"
    original = PersistentConversationStore(str(path))
    original.db.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE agent_af3_request_keys")
        db.execute("DELETE FROM core_schema_migrations WHERE version>=9")
        db.execute("PRAGMA user_version=8")

    upgraded = PersistentConversationStore(str(path))
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded.db.execute(
        "SELECT name FROM sqlite_master WHERE name='agent_af3_request_keys'"
    ).fetchone() == ("agent_af3_request_keys",)
    upgraded.db.close()


def test_version_nine_database_adds_model_call_reservations(tmp_path):
    path = tmp_path / "existing-v9.sqlite3"
    original = PersistentConversationStore(str(path))
    original.db.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE agent_model_call_guards")
        db.execute("DELETE FROM core_schema_migrations WHERE version>=10")
        db.execute("PRAGMA user_version=9")

    upgraded = PersistentConversationStore(str(path))
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded.db.execute(
        "SELECT name FROM sqlite_master WHERE name='agent_model_call_guards'"
    ).fetchone() == ("agent_model_call_guards",)
    upgraded.db.close()


def test_version_ten_database_backfills_legacy_af3_resource_profile(tmp_path):
    path = tmp_path / "existing-v10.sqlite3"
    original = PersistentConversationStore(str(path), af3_min_gpu_memory_mb=40_960)
    job = original.create_af3_job("alice", 20)
    original.db.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE agent_jobs DROP COLUMN resource_requirements_json")
        db.execute("DELETE FROM core_schema_migrations WHERE version>=11")
        db.execute("PRAGMA user_version=10")

    upgraded = PersistentConversationStore(str(path), af3_min_gpu_memory_mb=40_960)
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded.get_af3_job("alice", job.id).resource_requirements.min_gpu_memory_mb == 0
    upgraded.db.close()


def test_version_eleven_database_conservatively_backfills_claimed_gpu_usage(tmp_path):
    path = tmp_path / "existing-v11.sqlite3"
    original = PersistentConversationStore(str(path))
    claimed = original.create_af3_job("alice", 20)
    unclaimed = original.create_af3_job("alice", 20)
    direct_result = original.create_af3_job("alice", 20)
    original.db.execute(
        "UPDATE agent_jobs SET status='failed',attempts=1,actual_minutes=0 "
        "WHERE id=?", (claimed.id,),
    )
    original.db.execute(
        "UPDATE agent_jobs SET status='cancelled',attempts=0 WHERE id=?", (unclaimed.id,),
    )
    original.db.execute(
        "UPDATE agent_jobs SET status='failed',attempts=0,actual_minutes=9 WHERE id=?",
        (direct_result.id,),
    )
    original.db.commit()
    original.db.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE agent_jobs DROP COLUMN gpu_accounting_status")
        db.execute("DELETE FROM core_schema_migrations WHERE version>=12")
        db.execute("PRAGMA user_version=11")

    upgraded = PersistentConversationStore(str(path))
    held = upgraded.get_af3_job("alice", claimed.id)
    released = upgraded.get_af3_job("alice", unclaimed.id)
    settled = upgraded.get_af3_job("alice", direct_result.id)
    assert held.gpu_accounting_status == "pending_reconciliation"
    assert held.actual_gpu_minutes is None
    assert released.gpu_accounting_status == "released"
    assert settled.gpu_accounting_status == "settled"
    assert upgraded.usage_for("alice").gpu.used == 9
    assert upgraded.usage_for("alice").gpu.reserved == 20
    upgraded.db.close()


def test_version_twelve_database_adds_gpu_reconciliation_audit(tmp_path):
    path = tmp_path / "existing-v12.sqlite3"
    original = PersistentConversationStore(str(path))
    original.db.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE agent_gpu_reconciliations")
        db.execute("DELETE FROM core_schema_migrations WHERE version>=13")
        db.execute("PRAGMA user_version=12")

    upgraded = PersistentConversationStore(str(path))
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "source" in {row[1] for row in upgraded.db.execute(
        "PRAGMA table_info(agent_gpu_reconciliations)"
    )}
    upgraded.db.close()


def test_existing_current_schema_without_history_is_adopted_once(tmp_path):
    path = tmp_path / "existing-v13.sqlite3"
    first = PersistentConversationStore(str(path))
    first.db.execute("DELETE FROM core_schema_migrations")
    first.db.commit()
    first.db.close()

    second = PersistentConversationStore(str(path))
    third = PersistentConversationStore(str(path))
    history = third.db.execute(
        "SELECT version,description FROM core_schema_migrations"
    ).fetchall()
    assert history == [(SCHEMA_VERSION, "adopted existing schema")]
    second.db.close()
    third.db.close()
