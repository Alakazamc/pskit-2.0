import hashlib
import hmac
import sqlite3
from pathlib import Path
from time import time

from app.db.migrations import migrate_component_database
from app.db.postgres import PostgresDatabase, PostgresStatements


class GuestRateLimiter:
    """Share anonymous account creation limits through SQLite."""

    def __init__(self, path: str | PostgresDatabase, *, secret: str, limit_per_hour: int) -> None:
        """Open the rate-limit database and migrate its event table.

        Args:
            path: Shared SQLite database path.
            secret: HMAC key used to avoid storing raw client addresses.
            limit_per_hour: Maximum guest identities per client and hour.
        """
        if isinstance(path, PostgresDatabase):
            self.db = PostgresStatements(path)
        else:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.secret = secret.encode()
        self.limit_per_hour = limit_per_hour

        def create_events(db: sqlite3.Connection) -> None:
            """Create the client hash ledger and lookup index."""
            db.execute(
                "CREATE TABLE IF NOT EXISTS guest_creation_events ("
                "client_hash TEXT NOT NULL, created_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS guest_creation_events_lookup "
                "ON guest_creation_events(client_hash,created_at)"
            )

        if not isinstance(path, PostgresDatabase):
            migrate_component_database(self.db, "guest_rate_limit", (
                ("anonymous creation events", create_events),
            ), {"guest_creation_events": {"client_hash", "created_at"}})

    def claim(self, client_address: str) -> bool:
        """Atomically consume a guest creation slot for a client address.

        Old ledger rows are pruned after 24 hours; only an HMAC of the address
        is stored. The rolling admission window is one hour.

        Args:
            client_address: Network address used for the shared rate limit.

        Returns:
            Whether the caller received a creation slot.
        """
        client_hash = hmac.new(
            self.secret, client_address.encode(), hashlib.sha256,
        ).hexdigest()
        now = int(time())
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("DELETE FROM guest_creation_events WHERE created_at <= ?", (now - 86400,))
            count = self.db.execute(
                "SELECT COUNT(*) FROM guest_creation_events "
                "WHERE client_hash=? AND created_at>?",
                (client_hash, now - 3600),
            ).fetchone()[0]
            if count >= self.limit_per_hour:
                self.db.commit()
                return False
            self.db.execute(
                "INSERT INTO guest_creation_events (client_hash,created_at) VALUES (?,?)",
                (client_hash, now),
            )
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise
