import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from app.db.migrations import migrate_mcp_capacity_schema, migrate_pdf_capacity_schema
from app.ports.providers import ProviderUnavailable


class _SqliteCapacityStore:
    """An admission lease shared by Python instances using the same SQLite file."""

    config_table: str
    lease_table: str
    label: str
    migrate: Callable[[sqlite3.Connection], None]

    def __init__(self, path: str, max_calls: int) -> None:
        """Open a shared lease database and enforce one common capacity.

        Args:
            path: SQLite database shared by workers using this pool.
            max_calls: Maximum concurrent leases across those workers.

        Raises:
            ValueError: Another instance configured a different limit.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=0.05)
        try:
            self.migrate(self.db)
            self.db.execute("PRAGMA journal_mode=WAL")
            with self.db:
                self.db.execute(
                    f"INSERT OR IGNORE INTO {self.config_table} VALUES ('global',?)", (max_calls,),
                )
            configured = self.db.execute(
                f"SELECT max_calls FROM {self.config_table} WHERE scope='global'",
            ).fetchone()[0]
            if configured != max_calls:
                raise ValueError(f"{self.label} capacity must match other instances sharing this database")
        except BaseException:
            self.db.close()
            raise

    def claim(self, lease_seconds: float) -> str | None:
        """Atomically claim a free slot after removing expired leases.

        Args:
            lease_seconds: Time before an unrenewed slot expires.

        Returns:
            New lease token, or ``None`` when the pool is full or locked.

        Raises:
            ProviderUnavailable: SQLite cannot serve the capacity ledger.
        """
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    return None
                raise ProviderUnavailable(f"{self.label} capacity store unavailable") from exc
            try:
                now = time.time()
                self.db.execute(f"DELETE FROM {self.lease_table} WHERE expires_at<=?", (now,))
                count = self.db.execute(f"SELECT COUNT(*) FROM {self.lease_table}").fetchone()[0]
                limit = self.db.execute(
                    f"SELECT max_calls FROM {self.config_table} WHERE scope='global'",
                ).fetchone()[0]
                if count >= limit:
                    self.db.commit()
                    return None
                token = secrets.token_hex(16)
                self.db.execute(
                    f"INSERT INTO {self.lease_table} VALUES (?,?)",
                    (token, now + lease_seconds),
                )
                self.db.commit()
                return token
            except sqlite3.Error as exc:
                self.db.rollback()
                raise ProviderUnavailable(f"{self.label} capacity store unavailable") from exc

    def renew(self, token: str, lease_seconds: float) -> bool:
        """Extend an unexpired lease owned by its opaque token.

        Args:
            token: Lease token returned by :meth:`claim`.
            lease_seconds: New lease duration.

        Returns:
            Whether a live lease was extended.

        Raises:
            ProviderUnavailable: SQLite cannot update the lease.
        """
        with self._lock:
            try:
                with self.db:
                    return bool(self.db.execute(
                        f"UPDATE {self.lease_table} SET expires_at=? "
                        "WHERE token=? AND expires_at>?",
                        (time.time() + lease_seconds, token, time.time()),
                    ).rowcount)
            except sqlite3.Error as exc:
                raise ProviderUnavailable(f"{self.label} capacity store unavailable") from exc

    def release(self, token: str) -> None:
        """Release a lease token, allowing another request into the pool.

        Args:
            token: Lease token to remove.

        Raises:
            ProviderUnavailable: SQLite cannot update the lease.
        """
        with self._lock:
            try:
                with self.db:
                    self.db.execute(f"DELETE FROM {self.lease_table} WHERE token=?", (token,))
            except sqlite3.Error as exc:
                raise ProviderUnavailable(f"{self.label} capacity store unavailable") from exc


class McpCapacityStore(_SqliteCapacityStore):
    """Shared admission pool for remote MCP calls."""
    config_table = "mcp_capacity_config"
    lease_table = "mcp_execution_leases"
    label = "MCP"
    migrate = staticmethod(migrate_mcp_capacity_schema)


class PdfCapacityStore(_SqliteCapacityStore):
    """Shared admission pool for PDF parser subprocesses."""
    config_table = "pdf_capacity_config"
    lease_table = "pdf_execution_leases"
    label = "PDF"
    migrate = staticmethod(migrate_pdf_capacity_schema)
