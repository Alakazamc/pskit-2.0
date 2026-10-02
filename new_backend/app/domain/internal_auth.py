import secrets
import sqlite3
from pathlib import Path

from app.db.migrations import migrate_internal_tool_auth_schema


def load_or_create_internal_tool_secret(path: str) -> bytes:
    """All API instances using one agent database must verify the same Pi tool token."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        migrate_internal_tool_auth_schema(db)
        with db:
            db.execute(
                "INSERT OR IGNORE INTO internal_tool_auth VALUES ('hmac-sha256',?)",
                (secrets.token_bytes(32),),
            )
        secret = db.execute(
            "SELECT secret FROM internal_tool_auth WHERE id='hmac-sha256'",
        ).fetchone()[0]
        if not isinstance(secret, bytes) or len(secret) != 32:
            raise ValueError("Invalid internal tool auth secret")
        return secret
    finally:
        db.close()
