"""Fixtures for an isolated PostgreSQL schema per integration test."""

import os
import uuid

import pytest
from admin_support import admin_system  # noqa: F401
from compute_support import ledger_system  # noqa: F401


@pytest.fixture
def pg_schema() -> tuple[str, str]:
    """Yield a unique schema and remove it after the test."""
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN to run PostgreSQL integration tests")
    import psycopg
    from psycopg import sql

    schema = f"pskit_test_{uuid.uuid4().hex}"
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            owned = [name for (name,) in connection.execute(
                "SELECT nspname FROM pg_namespace"
            ) if name == schema or name.startswith(f"{schema}_preimport_")]
            for name in owned:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
