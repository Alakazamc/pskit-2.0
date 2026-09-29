"""Transaction-scoped admission locks shared by API and worker processes."""

import hashlib

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import SystemState


def acquire_transaction_lock(db: Session, name: str) -> None:
    """Hold a named lock until the caller commits or rolls back its transaction."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        lock_id = int.from_bytes(
            hashlib.sha256(name.encode()).digest()[:8], "big", signed=True
        )
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    elif dialect == "sqlite":
        # Even ON CONFLICT DO NOTHING acquires the database write lock.
        # No process-local mutex is needed; SQLite releases it on transaction end.
        db.execute(
            sqlite_insert(SystemState)
            .values(key=f"lock:{name}", value_json={})
            .on_conflict_do_nothing(index_elements=[SystemState.key])
        )
    else:
        raise RuntimeError(f"Unsupported admission-lock database: {dialect}")
