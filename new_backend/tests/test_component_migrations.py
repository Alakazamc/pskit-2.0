import sqlite3

import pytest

from app.api.auth import OAuthFlowStore
from app.domain.catalog import CatalogStore
from app.domain.tool_runs import ToolRunStore


def test_catalog_migration_rolls_back_and_other_components_keep_separate_versions(
    tmp_path, monkeypatch,
):
    from app.db import migrations

    path = tmp_path / "shared.sqlite3"
    original = migrations.add_column_if_missing

    def interrupted(db, table, name, declaration):
        if name == "raw_content":
            raise RuntimeError("catalog migration interrupted")
        return original(db, table, name, declaration)

    monkeypatch.setattr(migrations, "add_column_if_missing", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        CatalogStore(db_path=str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='catalog_files'").fetchall() == []
        assert db.execute("SELECT name FROM sqlite_master WHERE name='component_schema_migrations'").fetchall() == []

    monkeypatch.setattr(migrations, "add_column_if_missing", original)
    catalog = CatalogStore(db_path=str(path))
    oauth = OAuthFlowStore(str(path))
    tools = ToolRunStore(str(path))
    history = catalog.db.execute(
        "SELECT component,version FROM component_schema_migrations ORDER BY component,version"
    ).fetchall()
    assert history == [
        ("catalog", 1), ("catalog", 2), ("catalog", 3), ("oauth", 1), ("oauth", 2),
        ("tool_runs", 1),
    ]
    assert oauth.db.execute("SELECT name FROM sqlite_master WHERE name='oauth_flows'").fetchone()
    assert "guest_user_id" in {
        row[1] for row in oauth.db.execute("PRAGMA table_info(oauth_flows)").fetchall()
    }
    assert tools.db.execute("SELECT name FROM sqlite_master WHERE name='tool_runs'").fetchone()


def test_unknown_component_schema_version_is_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE component_schema_migrations ("
                   "component TEXT NOT NULL, version INTEGER NOT NULL, applied_at TEXT NOT NULL, "
                   "description TEXT NOT NULL, PRIMARY KEY(component,version))")
        db.execute("INSERT INTO component_schema_migrations VALUES ('catalog',99,'now','future')")

    with pytest.raises(ValueError, match="newer than this application"):
        CatalogStore(db_path=str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='catalog_files'").fetchall() == []


def test_component_with_current_marker_but_missing_column_fails_startup(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE component_schema_migrations ("
                   "component TEXT NOT NULL, version INTEGER NOT NULL, applied_at TEXT NOT NULL, "
                   "description TEXT NOT NULL, PRIMARY KEY(component,version))")
        db.execute("INSERT INTO component_schema_migrations VALUES ('catalog',3,'now','current')")
        db.execute("CREATE TABLE catalog_files (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                   "name TEXT NOT NULL, size INTEGER NOT NULL, content TEXT NOT NULL)")

    with pytest.raises(ValueError, match="missing columns"):
        CatalogStore(db_path=str(path))
