"""PostgreSQL connection pool for private Agent business state."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool


class PostgresDatabase:
    """Own pooled connections scoped to one private PostgreSQL schema."""

    def __init__(self, dsn: str, *, schema: str = "pskit", max_size: int = 10) -> None:
        """Open a pool whose connections resolve unqualified business tables privately."""
        if not dsn or not schema or max_size < 1:
            raise ValueError("PostgreSQL DSN, schema, and positive pool size are required")
        self.schema = schema
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=max_size,
            configure=self._configure,
            open=True,
        )
        self.pool.wait()

    def _configure(self, connection: psycopg.Connection) -> None:
        """Pin this pooled connection to the private schema."""
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema))
        )
        connection.commit()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Yield a pooled connection and commit or roll back on exit."""
        with self.pool.connection() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Yield one connection with an explicit atomic transaction."""
        with self.connection() as connection, connection.transaction():
            yield connection

    def check_schema_version(self, required: int) -> None:
        """Reject absent, incomplete, or newer schemas without modifying them."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass(%s)", (f"{self.schema}.schema_migrations",)
            ).fetchone()
            if row is None or row[0] is None:
                raise RuntimeError("PSKit PostgreSQL schema is not migrated")
            versions = [
                version for (version,) in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            if versions != list(range(1, required + 1)):
                raise RuntimeError(
                    f"PSKit PostgreSQL schema version mismatch: expected {required}, got {versions}"
                )

    def close(self) -> None:
        """Return all pool resources to PostgreSQL."""
        self.pool.close()
