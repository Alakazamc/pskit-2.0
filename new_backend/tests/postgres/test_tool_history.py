"""Per-tool history filtering on the production PostgreSQL adapter."""

from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.tool_runs import ToolRunStore


def test_tool_history_remains_exact_and_owner_scoped_after_reopen(pg_schema: tuple[str, str]) -> None:
    """Namespaces match exactly; other users' records remain private."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = ToolRunStore(database)
        first = store.add("alice", "lab/search", {"query": "p53"}, {})
        store.add("alice", "lab/search_extra", {}, {})
        latest = store.add("alice", "lab/search", {"query": "rna"}, {})
        store.add("bob", "lab/search", {}, {"private": True})
    finally:
        database.close()

    reopened = PostgresDatabase(dsn, schema=schema)
    try:
        history = ToolRunStore(reopened)
        assert [item.id for item in history.list_for("alice", "lab/search")] == [latest.id, first.id]
        assert history.list_for("alice", "missing") == []
        assert len(history.list_for("alice")) == 3
        assert len(history.list_for("bob", "lab/search")) == 1
    finally:
        reopened.close()
