"""PostgreSQL connection pool for private Agent business state."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import local
from typing import Self

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


@dataclass
class QueryResult:
    """A consumed result whose rows remain valid after pool check-in."""

    rows: list[tuple]
    rowcount: int

    def fetchone(self) -> tuple | None:
        """Return the first row, if present."""
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        """Return all rows from the completed query."""
        return self.rows

    def __iter__(self) -> Iterator[tuple]:
        """Iterate over the completed query."""
        return iter(self.rows)


class PostgresStatements:
    """Bridge existing repository statements to pooled PostgreSQL transactions.

    The bridge is private to the repository port. Domain SQL is converted
    explicitly during Tasks 2–6; it handles the shared parameter and ordering
    syntax while those modules are migrated.
    """

    def __init__(self, database: PostgresDatabase) -> None:
        """Keep transaction nesting local to the calling thread."""
        self.database = database
        self._local = local()

    def __enter__(self) -> Self:
        """Start a transaction or nested savepoint for repository methods."""
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        manager = self.database.transaction() if not stack else stack[-1][1].transaction()
        entered = manager.__enter__()
        connection = entered if not stack else stack[-1][1]
        stack.append((manager, connection))
        return self

    def __exit__(self, error_type, error, traceback) -> None:
        """Commit on success and roll back on an exception."""
        manager, _ = self._local.stack.pop()
        manager.__exit__(error_type, error, traceback)

    def execute(self, statement: str, parameters: tuple = ()) -> QueryResult:
        """Run one repository statement and detach its result from the pool."""
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            self.begin_immediate()
            return QueryResult([], 0)
        statement = re.sub(r"\browid\b", "ordinal", statement)
        statement = statement.replace("json_extract(e.payload, '$.type')", "(e.payload::jsonb ->> 'type')")
        statement = statement.replace("json_extract(payload, '$.type')", "(payload::jsonb ->> 'type')")
        statement = statement.replace("?", "%s")
        stack = getattr(self._local, "stack", ())
        if stack:
            return self._run(stack[-1][1], statement, parameters)
        with self.database.connection() as connection:
            return self._run(connection, statement, parameters)

    @staticmethod
    def _run(connection: psycopg.Connection, statement: str, parameters: tuple) -> QueryResult:
        """Materialize rows before returning the connection to its pool."""
        cursor = connection.execute(statement, parameters)
        rows = cursor.fetchall() if cursor.description else []
        return QueryResult(rows, cursor.rowcount)

    def executemany(self, statement: str, parameters: list[tuple]) -> None:
        """Execute several parameter sets in one transaction."""
        with self:
            for arguments in parameters:
                self.execute(statement, arguments)

    def begin_immediate(self) -> None:
        """Start a manually committed transaction with a shared admission lock."""
        if getattr(self._local, "stack", ()):
            raise RuntimeError("A transaction is already active")
        manager = self.database.connection()
        connection = manager.__enter__()
        self._local.stack = [(manager, connection)]
        try:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission', 0))"
            )
        except BaseException as error:
            self._local.stack.pop()
            manager.__exit__(type(error), error, error.__traceback__)
            raise

    def commit(self) -> None:
        """Finish a manual transaction, if one is open."""
        stack = getattr(self._local, "stack", ())
        if stack:
            manager, connection = stack.pop()
            try:
                connection.commit()
            finally:
                manager.__exit__(None, None, None)

    def rollback(self) -> None:
        """Discard a manual transaction, if one is open."""
        stack = getattr(self._local, "stack", ())
        if stack:
            manager, connection = stack.pop()
            try:
                connection.rollback()
            finally:
                manager.__exit__(None, None, None)

    def close(self) -> None:
        """Leave the externally owned pool open."""
        self.rollback()
