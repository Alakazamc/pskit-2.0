"""Create PSKit and LiteLLM roles on the existing Supabase PostgreSQL server.

Run with an administrative DSN after Supabase is healthy. Credentials are read
from a private environment file; no database password belongs in argv or logs.
"""

from __future__ import annotations

import os

import psycopg
from psycopg import sql


def _ensure_role(connection: psycopg.Connection, name: str, password: str) -> None:
    if not connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,),
    ).fetchone():
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password),
            )
        )
    else:
        connection.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password),
            )
        )


def provision_shared_postgres(
    admin_dsn: str, *, litellm_password: str, pskit_password: str,
) -> None:
    """Idempotently provision two restricted applications on one PG instance."""
    if not admin_dsn or len(litellm_password) < 16 or len(pskit_password) < 16:
        raise ValueError("Admin DSN and strong application passwords are required")
    if litellm_password == pskit_password:
        raise ValueError("LiteLLM and PSKit must use different passwords")

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        _ensure_role(admin, "litellm", litellm_password)
        _ensure_role(admin, "pskit_app", pskit_password)
        if not admin.execute(
            "SELECT 1 FROM pg_database WHERE datname='litellm'"
        ).fetchone():
            admin.execute("CREATE DATABASE litellm")
        admin.execute("REVOKE ALL ON DATABASE litellm FROM PUBLIC")
        admin.execute("GRANT CONNECT, CREATE, TEMPORARY ON DATABASE litellm TO litellm")
        admin.execute("REVOKE ALL ON DATABASE litellm FROM pskit_app")
        admin.execute("CREATE SCHEMA IF NOT EXISTS pskit")
        admin.execute("GRANT USAGE ON SCHEMA pskit TO pskit_app")
        admin.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pskit TO pskit_app"
        )
        admin.execute(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA pskit TO pskit_app"
        )
        admin.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA pskit "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pskit_app"
        )
        admin.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA pskit "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO pskit_app"
        )

    litellm_dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname="litellm")
    with psycopg.connect(litellm_dsn, autocommit=True) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        admin.execute("GRANT ALL ON SCHEMA public TO litellm")


def main() -> None:
    provision_shared_postgres(
        os.environ["SHARED_POSTGRES_ADMIN_DSN"],
        litellm_password=os.environ["LITELLM_DB_PASSWORD"],
        pskit_password=os.environ["PSKIT_DB_PASSWORD"],
    )
    print("Shared PostgreSQL roles and LiteLLM database are ready")


if __name__ == "__main__":
    main()
