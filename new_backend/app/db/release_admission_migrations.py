"""Explicit admission component installation; core schema contracts stay unchanged."""

import time
import uuid
from datetime import UTC, datetime
from importlib.resources import files

import psycopg
from psycopg import sql

COMPONENT_VERSION = 1


def install_admission_component(connection, *, postgres: bool) -> None:
    """Install once inside the caller's explicit migration transaction.

    Reinstallation never overwrites owner state. No runtime service invokes
    this DDL; old API/core schema compatibility is tracked independently.
    """
    if postgres:
        exists = connection.execute("SELECT to_regclass('release_admission_migrations')").fetchone()[0]
    else:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='release_admission_migrations'",
        ).fetchone()
    if exists:
        versions = [row[0] for row in connection.execute(
            "SELECT version FROM release_admission_migrations ORDER BY version",
        )]
        if versions != [COMPONENT_VERSION]:
            raise ValueError("Admission component migration version is unsupported")
        return
    statement = files("app.db").joinpath("release_admission_v1.sql").read_text(encoding="utf-8")
    for part in statement.split(";"):
        if part.strip():
            connection.execute(part)
    placeholder = "%s" if postgres else "?"
    connection.execute(
        "INSERT INTO release_admission_state (singleton,component_version,deployment_id,state,updated_at) "
        f"VALUES (1,1,{placeholder},'open',{placeholder})",
        (uuid.uuid4().hex, time.time()),
    )
    connection.execute(
        f"INSERT INTO release_admission_migrations VALUES (1,{placeholder})",
        (datetime.now(UTC).isoformat(),),
    )


def migrate_admission_postgres(dsn: str, *, schema: str = "pskit") -> None:
    """Explicitly install the separately versioned component in an existing schema."""
    with psycopg.connect(dsn) as connection, connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           (f"pskit-migrations:{schema}",))
        connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        install_admission_component(connection, postgres=True)


def migrate_admission_sqlite(connection) -> None:
    """Install only in an explicitly selected offline SQLite database."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        install_admission_component(connection, postgres=False)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
