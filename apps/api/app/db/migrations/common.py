"""Common SQLite schema migrations."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime


def add_column_if_missing(
    db: sqlite3.Connection, table: str, name: str, declaration: str,
) -> None:
    """Add a trusted schema column only when an older database lacks it.

    Args:
        db: Open SQLite connection; the caller owns its transaction.
        table: Internal table name to inspect.
        name: Internal column name to add.
        declaration: SQLite column declaration appended to ALTER TABLE.
    """
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

def migrate_component_database(
    db: sqlite3.Connection, component: str,
    steps: tuple[tuple[str, Callable[[sqlite3.Connection], None]], ...],
    expected_columns: dict[str, set[str]],
) -> None:
    """Apply and verify one component's migrations atomically.

    Component versions live in ``component_schema_migrations`` rather than
    ``PRAGMA user_version``. A failed step rolls back the whole transaction.

    Args:
        db: Open SQLite connection.
        component: Stable component key in the migration ledger.
        steps: Ordered descriptions and migration functions.
        expected_columns: Required columns in the final schema.

    Raises:
        ValueError: The database is newer than this code or lacks required columns.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS component_schema_migrations ("
            "component TEXT NOT NULL, version INTEGER NOT NULL, applied_at TEXT NOT NULL, "
            "description TEXT NOT NULL, PRIMARY KEY(component,version))"
        )
        current = db.execute(
            "SELECT COALESCE(MAX(version),0) FROM component_schema_migrations WHERE component=?",
            (component,),
        ).fetchone()[0]
        if current > len(steps):
            raise ValueError(f"{component} database schema is newer than this application")
        for version in range(current + 1, len(steps) + 1):
            description, apply = steps[version - 1]
            apply(db)
            db.execute(
                "INSERT INTO component_schema_migrations VALUES (?,?,?,?)",
                (component, version, datetime.now(UTC).isoformat(), description),
            )
        for table, expected in expected_columns.items():
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not expected <= columns:
                raise ValueError(f"{component} database schema is missing columns in {table}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def call_add_column_if_missing(
    db: sqlite3.Connection, table: str, name: str, declaration: str,
) -> None:
    """Apply a column migration through the stable public hook.

    Args:
        db: Open SQLite connection.
        table: Internal table name.
        name: Internal column name.
        declaration: SQLite column declaration.
    """
    from . import add_column_if_missing as public_add_column

    public_add_column(db, table, name, declaration)
