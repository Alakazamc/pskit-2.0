from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.session import engine, init_db
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text


def verify_expanded_schema(tables: set[str]) -> None:
    """Do not bless a partial legacy schema merely because one table exists."""
    from app.db import harness_models, models  # noqa: F401

    inspector = inspect(engine)
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue  # init_db creates new tables, but cannot alter existing ones.
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        absent = set(table.columns.keys()) - actual
        if absent:
            missing.append(f"{table.name}: {', '.join(sorted(absent))}")
            continue
        primary_key = inspector.get_pk_constraint(table.name).get("constrained_columns") or []
        if set(primary_key) != {column.name for column in table.primary_key.columns}:
            missing.append(f"{table.name}: incompatible primary key")
        indexes = inspector.get_indexes(table.name)
        actual_unique = {
            tuple(sorted(item["column_names"]))
            for item in inspector.get_unique_constraints(table.name)
        } | {
            tuple(sorted(item["column_names"]))
            for item in indexes if item.get("unique")
        }
        expected_unique = {
            tuple(sorted(column.name for column in constraint.columns))
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
        } | {
            tuple(sorted(column.name for column in index.columns))
            for index in table.indexes if index.unique
        }
        for columns in sorted(expected_unique - actual_unique):
            missing.append(f"{table.name}: missing unique constraint on {columns}")
        actual_indexes = {tuple(item["column_names"]) for item in indexes}
        for index in table.indexes:
            columns = tuple(column.name for column in index.columns)
            if columns not in actual_indexes:
                missing.append(f"{table.name}: missing index on {columns}")
        actual_foreign_keys = {
            (tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
            for item in inspector.get_foreign_keys(table.name)
        }
        for foreign_key in table.foreign_key_constraints:
            signature = (
                tuple(element.parent.name for element in foreign_key.elements),
                foreign_key.referred_table.name,
                tuple(element.column.name for element in foreign_key.elements),
            )
            if signature not in actual_foreign_keys:
                missing.append(f"{table.name}: missing foreign key {signature}")
        actual_checks = {item["name"] for item in inspector.get_check_constraints(table.name)}
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name not in actual_checks:
                missing.append(f"{table.name}: missing check constraint {constraint.name}")
    if missing:
        raise RuntimeError(
            "Existing expanded schema is incomplete; refusing to stamp migration "
            "0003. Restore or explicitly migrate missing columns/constraints: " + "; ".join(missing)
        )


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    config = Config(str(backend_dir / "alembic.ini"))
    tables = set(inspect(engine).get_table_names())
    revision = None
    if "alembic_version" in tables:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

    # The 0.2.2 production image created the expanded schema at application
    # startup before it carried the matching Alembic revision.  Preserve those
    # live tables, create only newly added checkfirst tables, then align the
    # revision marker instead of replaying CREATE TABLE statements over data.
    if "agent_turns" in tables and revision in {None, "0001", "0002"}:
        verify_expanded_schema(tables)
        init_db()
        # This compatibility bridge describes 0003 specifically. Future
        # revisions must run normally rather than being skipped by stamp(head).
        command.stamp(config, "0003")
        command.upgrade(config, "head")
        print("expanded production schema verified and migrations applied")
        return

    if "alembic_version" not in tables:
        if "users" not in tables:
            init_db()
            command.stamp(config, "head")
            print("database initialized at migration head")
            return

        task_columns = {
            column["name"] for column in inspect(engine).get_columns("tasks")
        }
        if "app_state" in tables and "attempt_count" in task_columns:
            command.stamp(config, "0002")
            command.upgrade(config, "head")
            print("existing hardened database upgraded to migration head")
            return

        command.stamp(config, "0001")

    command.upgrade(config, "head")
    print("database migrations complete")


if __name__ == "__main__":
    main()
