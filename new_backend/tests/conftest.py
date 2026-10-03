"""Shared live-mode PostgreSQL fixture for HTTP tests."""

import os
import uuid

import pytest


@pytest.fixture
def live_database(monkeypatch: pytest.MonkeyPatch):
    """Give each live HTTP case a migrated, isolated private schema."""
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN for live PostgreSQL integration tests")
    import psycopg
    from psycopg import sql

    from app import main as app_main
    from app.db.postgres import PostgresDatabase
    from app.db.postgres_migrations import migrate_postgres

    schema = f"pskit_test_{uuid.uuid4().hex}"
    migrate_postgres(dsn, schema=schema)
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_URL", dsn)
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_SCHEMA", schema)
    opened: list[PostgresDatabase] = []

    def tracked_database(*args, **kwargs) -> PostgresDatabase:
        database = PostgresDatabase(*args, **kwargs)
        opened.append(database)
        return database

    monkeypatch.setattr(app_main, "PostgresDatabase", tracked_database)
    try:
        yield dsn, schema
    finally:
        for database in opened:
            database.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
