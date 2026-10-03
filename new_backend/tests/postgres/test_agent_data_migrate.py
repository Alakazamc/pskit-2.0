"""Offline SQLite snapshot import and reversible PostgreSQL export."""

import hashlib
import sqlite3
from pathlib import Path

import psycopg
import pytest
from deploy.agent.scripts.agent_data_snapshot import snapshot
from psycopg import sql

from app.db.postgres_migrations import migrate_postgres
from app.domain.catalog import CatalogStore
from app.domain.identity_policy import IdentityPolicyStore
from app.domain.persistent_conversation import PersistentConversationStore
from scripts import agent_data_migrate


def _sample_snapshot(root: Path) -> tuple[Path, bytes]:
    source = root / "old-volume"
    source.mkdir()
    database = source / "agent.sqlite3"
    store = PersistentConversationStore(str(database))
    IdentityPolicyStore(str(database)).observe_verified_user("alice", False)
    catalog = CatalogStore(db_path=str(database))
    catalog.add_uploaded_file("alice", "notes.txt", b"alpha\x00beta")
    project = store.create_project("alice", "Research", "")
    session = store.create_session("alice", project.id, "Study")
    assert session is not None
    transcript = source / "pi-sessions" / session.id / "turn.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b'{"message":"hello"}\n')
    blob = b"\x00\xffaf3-result\x00"
    same_time = "2026-10-01T08:00:00+00:00"
    store.db.execute(
        "INSERT INTO pi_sessions (session_id,user_id,session_file) VALUES (?,?,?)",
        (session.id, "alice", f"/data/pi-sessions/{session.id}/turn.jsonl"),
    )
    store.db.execute(
        "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,checkpoint_file) "
        "VALUES (?,?,?,?,?,?)",
        ("run-1", "alice", session.id, "completed", same_time,
         f"/data/pi-sessions/{session.id}/turn.jsonl"),
    )
    for number in (1, 2):
        store.db.execute(
            "INSERT INTO agent_messages (id,user_id,session_id,role,content,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"message-{number}", "alice", session.id, "user", f"message {number}", same_time),
        )
    store.db.execute(
        "INSERT INTO agent_jobs "
        "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,artifacts,created_at) "
        "VALUES ('job-1','alice','run-1','tool-1','completed',100,20,'[]',?)",
        (same_time,),
    )
    store.db.execute(
        "INSERT INTO agent_artifact_blobs "
        "(id,user_id,job_id,name,kind,size,sha256,content,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("artifact-1", "alice", "job-1", "result.bin", "binary", len(blob),
         hashlib.sha256(blob).hexdigest(), blob, same_time),
    )
    store.db.execute(
        "INSERT INTO agent_token_entries "
        "(id,user_id,period,kind,amount,run_id,created_at,status) "
        "VALUES (27,'alice','2026-10','reservation',100,'run-1',?,'active')",
        (same_time,),
    )
    store.db.execute(
        "INSERT INTO agent_token_usage (user_id,period,used) VALUES ('alice','2026-10',100)"
    )
    store.db.commit()
    store.db.close()
    archive = root / "snapshot"
    snapshot(source, archive)
    return archive, blob


def test_import_preserves_all_tables_bytes_and_order(
    pg_schema: tuple[str, str], tmp_path: Path,
) -> None:
    """Import keeps every row, stable ordinal, bytea, quota, and Pi mapping."""
    dsn, target = pg_schema
    migrate_postgres(dsn, schema=target)
    archive, blob = _sample_snapshot(tmp_path)
    pi_root = tmp_path / "new-pi-sessions"
    agent_data_migrate.copy_pi_transcripts(archive, pi_root)
    report = agent_data_migrate.import_sqlite_snapshot(
        archive / "agent.sqlite3", dsn,
        staging_schema=f"{target}_stage", target_schema=target, pi_root=pi_root,
    )
    assert report.table_counts["agent_messages"] == 2
    assert report.table_counts["agent_artifact_blobs"] == 1
    assert report.source_sha256
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(target)))
        assert connection.execute(
            "SELECT id FROM agent_messages ORDER BY ordinal"
        ).fetchall() == [("message-1",), ("message-2",)]
        session_id = next((archive / "pi-sessions").iterdir()).name
        connection.execute(
            "INSERT INTO agent_messages "
            "(id,user_id,session_id,role,content,created_at) "
            "VALUES (%s,'alice',%s,'user','later','now')",
            ("message-3", session_id),
        )
        assert connection.execute(
            "SELECT id,ordinal FROM agent_messages ORDER BY ordinal"
        ).fetchall() == [("message-1", 1), ("message-2", 2), ("message-3", 3)]
        assert connection.execute(
            "SELECT content FROM agent_artifact_blobs WHERE id='artifact-1'"
        ).fetchone() == (blob,)
        assert connection.execute(
            "SELECT id,amount,status FROM agent_token_entries"
        ).fetchone() == (27, 100, "active")
        assert connection.execute(
            "SELECT session_file FROM pi_sessions"
        ).fetchone()[0].startswith(str(pi_root))
        assert connection.execute(
            "SELECT checkpoint_file FROM agent_runs WHERE id='run-1'"
        ).fetchone()[0].startswith(str(pi_root))
        assert connection.execute(
            "SELECT content,raw_content FROM catalog_files"
        ).fetchone() == ("alpha\ufffdbeta", b"alpha\x00beta")
        connection.execute(
            "INSERT INTO agent_token_entries "
            "(user_id,period,kind,amount,created_at) "
            "VALUES ('alice','2026-10','adjustment',1,'now')"
        )
        assert connection.execute(
            "SELECT MAX(id) FROM agent_token_entries"
        ).fetchone() == (28,)
    assert (pi_root / next((archive / "pi-sessions").iterdir()).name / "turn.jsonl").is_file()


def test_export_round_trip_and_next_insert(pg_schema: tuple[str, str], tmp_path: Path) -> None:
    """A fresh SQLite export remains readable and its autoincrement advances."""
    dsn, target = pg_schema
    migrate_postgres(dsn, schema=target)
    archive, blob = _sample_snapshot(tmp_path)
    pi_root = tmp_path / "new-pi"
    agent_data_migrate.copy_pi_transcripts(archive, pi_root)
    agent_data_migrate.import_sqlite_snapshot(
        archive / "agent.sqlite3", dsn,
        staging_schema=f"{target}_stage", target_schema=target, pi_root=pi_root,
    )
    destination = tmp_path / "restored.sqlite3"
    report = agent_data_migrate.export_sqlite_snapshot(dsn, destination, schema=target)
    assert report.table_counts["agent_messages"] == 2
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT id FROM agent_messages ORDER BY rowid"
        ).fetchall() == [("message-1",), ("message-2",)]
        assert connection.execute(
            "SELECT content FROM agent_artifact_blobs WHERE id='artifact-1'"
        ).fetchone() == (blob,)
        connection.execute(
            "INSERT INTO agent_token_entries "
            "(user_id,period,kind,amount,created_at) "
            "VALUES ('alice','2026-10','adjustment',1,'now')"
        )
        assert connection.execute("SELECT MAX(id) FROM agent_token_entries").fetchone() == (28,)
    reopened = PersistentConversationStore(str(destination))
    assert len(reopened.messages_for("alice", next((archive / "pi-sessions").iterdir()).name)) == 2
    reopened.db.close()


def test_failed_import_keeps_active_schema(
    pg_schema: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staging copy failure cannot modify the formal pskit schema."""
    dsn, target = pg_schema
    migrate_postgres(dsn, schema=target)
    archive, _ = _sample_snapshot(tmp_path)

    def interrupted(*_args, **_kwargs):
        raise RuntimeError("copy interrupted")

    monkeypatch.setattr(agent_data_migrate, "_copy_rows", interrupted)
    with pytest.raises(RuntimeError, match="copy interrupted"):
        agent_data_migrate.import_sqlite_snapshot(
            archive / "agent.sqlite3", dsn,
            staging_schema=f"{target}_stage", target_schema=target,
        )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.agent_messages").format(sql.Identifier(target))
        ).fetchone() == (0,)
        assert connection.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.schema_migrations").format(sql.Identifier(target))
        ).fetchone() == (3,)


def test_pi_copy_into_existing_empty_volume(tmp_path: Path) -> None:
    """A Docker named volume already has a mounted root directory."""
    archive, _ = _sample_snapshot(tmp_path)
    mounted_root = tmp_path / "mounted-pi-sessions"
    mounted_root.mkdir()
    assert agent_data_migrate.copy_pi_transcripts(archive, mounted_root) == 1
    assert len(list(mounted_root.rglob("*.jsonl"))) == 1


def test_import_requires_a_verified_snapshot_manifest(
    pg_schema: tuple[str, str], tmp_path: Path,
) -> None:
    """A bare SQLite file may omit WAL or Pi files and is not a cutover source."""
    dsn, target = pg_schema
    migrate_postgres(dsn, schema=target)
    plain = tmp_path / "agent.sqlite3"
    store = PersistentConversationStore(str(plain))
    store.db.close()
    with pytest.raises(ValueError, match="manifest"):
        agent_data_migrate.import_sqlite_snapshot(
            plain, dsn, staging_schema=f"{target}_stage", target_schema=target,
        )
