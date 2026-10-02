import sqlite3

import pytest

from scripts.sqlite_maintenance import (
    backup_database,
    backup_workspace_bundle,
    restore_database,
    restore_workspace_bundle,
)


def _value(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT value FROM records").fetchone()[0]


def test_online_backup_and_offline_restore_are_consistent_and_refuse_overwrite(tmp_path):
    source = tmp_path / "active.sqlite3"
    backup = tmp_path / "backups" / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE records (value TEXT)")
        db.execute("INSERT INTO records VALUES ('before')")

    backup_database(source, backup)
    assert _value(backup) == "before"
    with sqlite3.connect(source) as db:
        db.execute("UPDATE records SET value='after'")
    assert _value(backup) == "before"

    restore_database(backup, restored)
    assert _value(restored) == "before"
    with pytest.raises(FileExistsError):
        backup_database(source, backup)
    with pytest.raises(FileExistsError):
        restore_database(backup, restored)


def test_maintenance_rejects_missing_or_same_database_path(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    target = tmp_path / "target.sqlite3"
    with pytest.raises(FileNotFoundError):
        backup_database(missing, target)
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE records (value TEXT)")
    with pytest.raises(ValueError, match="same path"):
        backup_database(target, target, force=True)


def test_workspace_bundle_restores_database_and_pi_sessions_together(tmp_path):
    source = tmp_path / "live.sqlite3"
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    (sessions / "session-1.jsonl").write_text("before\n", encoding="utf-8")
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE records (value TEXT)")
        db.execute("INSERT INTO records VALUES ('before')")
    bundle = tmp_path / "snapshot"
    backup_workspace_bundle(source, sessions, bundle)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE records SET value='after'")
    (sessions / "session-1.jsonl").write_text("after\n", encoding="utf-8")

    restored_db = tmp_path / "restored.sqlite3"
    restored_sessions = tmp_path / "restored-pi"
    restore_workspace_bundle(bundle, restored_db, restored_sessions)
    assert _value(restored_db) == "before"
    assert (restored_sessions / "session-1.jsonl").read_text(encoding="utf-8") == "before\n"
    with pytest.raises(FileExistsError):
        restore_workspace_bundle(bundle, restored_db, restored_sessions)


def test_workspace_bundle_detects_tampering_and_rejects_session_symlinks(tmp_path):
    source = tmp_path / "live.sqlite3"
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    (sessions / "session.jsonl").write_text("original", encoding="utf-8")
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE records (value TEXT)")
        db.execute("INSERT INTO records VALUES ('before')")
    (sessions / "outside-link").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        backup_workspace_bundle(source, sessions, tmp_path / "bad")
    (sessions / "outside-link").unlink()

    bundle = tmp_path / "snapshot"
    backup_workspace_bundle(source, sessions, bundle)
    (bundle / "pi-sessions" / "session.jsonl").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        restore_workspace_bundle(bundle, tmp_path / "restored.sqlite3", tmp_path / "restored-pi")
    assert not (tmp_path / "restored.sqlite3").exists()
    assert not (tmp_path / "restored-pi").exists()


def test_workspace_bundle_rejects_destination_inside_live_session_tree(tmp_path):
    source = tmp_path / "agent.sqlite3"
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE records (value TEXT)")
    with pytest.raises(ValueError, match="inside"):
        backup_workspace_bundle(source, sessions, sessions / "snapshot")


def test_workspace_restore_rejects_database_inside_target_session_tree(tmp_path):
    source = tmp_path / "live.sqlite3"
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE records (value TEXT)")
    bundle = tmp_path / "snapshot"
    backup_workspace_bundle(source, sessions, bundle)
    target_sessions = tmp_path / "restored-pi"
    with pytest.raises(ValueError, match="overlap"):
        restore_workspace_bundle(bundle, target_sessions / "agent.sqlite3", target_sessions)


def test_workspace_restore_rejects_session_tree_inside_target_database_path(tmp_path):
    source = tmp_path / "live.sqlite3"
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE records (value TEXT)")
    bundle = tmp_path / "snapshot"
    backup_workspace_bundle(source, sessions, bundle)
    target_db = tmp_path / "restored-db"
    with pytest.raises(ValueError, match="overlap"):
        restore_workspace_bundle(bundle, target_db, target_db / "pi-sessions")
    assert not target_db.exists()
