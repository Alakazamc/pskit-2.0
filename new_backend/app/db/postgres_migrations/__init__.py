"""Explicit, versioned migrations for the private PSKit PostgreSQL schema."""

from importlib.resources import files

import psycopg
from psycopg import sql

SCHEMA_VERSION = 10


def migrate_postgres(dsn: str, *, schema: str = "pskit") -> None:
    """Apply pending SQL files atomically under a schema-specific advisory lock."""
    if not dsn or not schema:
        raise ValueError("PostgreSQL DSN and schema are required")
    with psycopg.connect(dsn) as connection, connection.transaction():
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"pskit-migrations:{schema}",),
        )
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )
        connection.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version integer PRIMARY KEY, applied_at text NOT NULL "
            "DEFAULT (to_char(clock_timestamp() AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')))"
        )
        applied = {
            version for (version,) in connection.execute(
                "SELECT version FROM schema_migrations"
            )
        }
        if applied - set(range(1, SCHEMA_VERSION + 1)):
            raise RuntimeError("PostgreSQL schema is newer than this application")
        for version, filename in (
            (1, "001_core.sql"), (2, "002_components.sql"), (3, "003_quotas.sql"),
            (4, "004_compute.sql"),
            (5, "005_sandboxes.sql"), (6, "006_admin.sql"),
            (7, "007_session_titles.sql"), (8, "008_auth_abuse.sql"),
            (9, "009_tool_products.sql"),
            (10, "010_compute_bindings.sql"),
        ):
            if version in applied:
                continue
            statement = files(__package__).joinpath(filename).read_text(encoding="utf-8")
            connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
            )
