from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_legacy_database_is_upgraded_and_bootstraps_admin(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id CHAR(32) PRIMARY KEY,
                username VARCHAR(80) NOT NULL,
                password_hash TEXT NOT NULL,
                role VARCHAR(20) NOT NULL,
                created_at DATETIME NOT NULL,
                disabled_at DATETIME
            );
            CREATE TABLE tasks (
                id CHAR(32) PRIMARY KEY,
                user_id CHAR(32) NOT NULL,
                task_type VARCHAR(80) NOT NULL,
                status VARCHAR(40) NOT NULL
            );
            INSERT INTO users
                (id, username, password_hash, role, created_at)
            VALUES
                ('00000000000000000000000000000001', 'legacy-user', 'unused', 'user', '2025-01-01');
            """
        )

    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=f"sqlite:///{database.as_posix()}",
        DATA_DIR=str(tmp_path),
        ARTIFACT_DIR=str(tmp_path / "artifacts"),
    )
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "migrate_db.py")],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        role = connection.execute("SELECT role FROM users WHERE username='legacy-user'").fetchone()
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert "attempt_count" in task_columns
    assert "app_state" in tables
    assert role == ("admin",)
    assert version == ("0002",)
