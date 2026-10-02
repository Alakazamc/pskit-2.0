"""Agent SQLite WAL and Pi transcripts survive a verified volume migration."""

from pathlib import Path
import importlib.util
import shutil
import sqlite3

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/agent_data_snapshot.py"
spec = importlib.util.spec_from_file_location("agent_data_snapshot", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
restore = module.restore
snapshot = module.snapshot


def test_snapshot_includes_committed_wal_and_pi_files(tmp_path: Path):
    source = tmp_path / "cloud-volume"
    source.mkdir()
    transcript = source / "pi-sessions" / "session.jsonl"
    transcript.parent.mkdir()
    transcript.write_bytes(b'{"message":"old session"}\n')
    connection = sqlite3.connect(source / "agent.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE messages (body TEXT NOT NULL)")
        connection.execute("INSERT INTO messages VALUES ('committed WAL row')")
        connection.commit()
        assert (source / "agent.sqlite3-wal").stat().st_size > 0

        saved = tmp_path / "snapshot"
        manifest = snapshot(source, saved)
        assert set(manifest) == {"agent.sqlite3", "pi-sessions/session.jsonl"}
        assert len(manifest["agent.sqlite3"]) == 64

        target = tmp_path / "a6000-volume"
        restore(saved, target)
        with sqlite3.connect(target / "agent.sqlite3") as restored:
            assert restored.execute("SELECT body FROM messages").fetchall() == [
                ("committed WAL row",),
            ]
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (target / "pi-sessions/session.jsonl").read_bytes() == transcript.read_bytes()
    finally:
        connection.close()


def test_restore_rejects_checksum_mismatch_and_nonempty_target(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    with sqlite3.connect(source / "agent.sqlite3") as connection:
        connection.execute("CREATE TABLE messages (body TEXT)")
    (source / "pi-sessions").mkdir()
    (source / "pi-sessions" / "session.jsonl").write_text("original")
    saved = tmp_path / "snapshot"
    snapshot(source, saved)

    target = tmp_path / "target"
    target.mkdir()
    (target / "existing").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        restore(saved, target)
    assert (target / "existing").read_text() == "keep"

    (saved / "pi-sessions" / "session.jsonl").write_text("tampered")
    clean_target = tmp_path / "clean-target"
    with pytest.raises(ValueError, match="checksum"):
        restore(saved, clean_target)
    assert not clean_target.exists()


def test_snapshot_reads_wal_from_read_only_volume_without_shm(tmp_path: Path):
    original = tmp_path / "live"
    original.mkdir()
    connection = sqlite3.connect(original / "agent.sqlite3")
    readonly = tmp_path / "readonly-volume"
    readonly.mkdir()
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE events (value TEXT)")
        connection.execute("INSERT INTO events VALUES ('durable')")
        connection.commit()
        shutil.copy2(original / "agent.sqlite3", readonly / "agent.sqlite3")
        shutil.copy2(original / "agent.sqlite3-wal", readonly / "agent.sqlite3-wal")
        readonly.chmod(0o500)
        saved = tmp_path / "readonly-snapshot"
        snapshot(readonly, saved)
        with sqlite3.connect(saved / "agent.sqlite3") as backed_up:
            assert backed_up.execute("SELECT value FROM events").fetchone() == ("durable",)
    finally:
        readonly.chmod(0o700)
        connection.close()
