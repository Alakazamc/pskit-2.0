from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from app.db.session import engine, init_db
from sqlalchemy import inspect


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    config = Config(str(backend_dir / "alembic.ini"))
    tables = set(inspect(engine).get_table_names())

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
            command.stamp(config, "head")
            print("existing hardened database stamped at migration head")
            return

        command.stamp(config, "0001")

    command.upgrade(config, "head")
    print("database migrations complete")


if __name__ == "__main__":
    main()
