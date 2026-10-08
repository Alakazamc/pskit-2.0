import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from app.db.migrations import migrate_internal_tool_auth_schema
from app.db.postgres import PostgresDatabase


@dataclass(frozen=True, slots=True)
class WorkspaceToolClaims:
    """Identity and expiry bound into one internal workspace tool token."""

    user_id: str
    run_id: str
    session_id: str
    attempt_id: str
    expires_at: int


class WorkspaceToolTokenCodec:
    """Issue compact HMAC claims without exposing provider credentials."""

    VERSION = 1
    MAX_TTL_SECONDS = 900

    def __init__(self, secret: bytes, *, clock=time.time) -> None:
        if len(secret) != 32:
            raise ValueError("Invalid internal tool auth secret")
        self.secret = secret
        self.clock = clock

    @staticmethod
    def _encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)

    def issue(
        self,
        *,
        user_id: str,
        run_id: str,
        session_id: str,
        attempt_id: str,
        ttl_seconds: int = MAX_TTL_SECONDS,
    ) -> str:
        if not 1 <= ttl_seconds <= self.MAX_TTL_SECONDS:
            raise ValueError("Workspace tool token TTL is invalid")
        payload = json.dumps(
            {
                "v": self.VERSION,
                "sub": user_id,
                "run": run_id,
                "session": session_id,
                "attempt": attempt_id,
                "exp": int(self.clock()) + ttl_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return f"{self._encode(payload)}.{self._encode(signature)}"

    def verify(self, token: str) -> WorkspaceToolClaims | None:
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = self._decode(encoded_payload)
            signature = self._decode(encoded_signature)
            expected = hmac.new(self.secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, signature):
                return None
            value = json.loads(payload)
            if set(value) != {"v", "sub", "run", "session", "attempt", "exp"}:
                return None
            if value["v"] != self.VERSION or type(value["exp"]) is not int:
                return None
            identifiers = (value["sub"], value["run"], value["session"], value["attempt"])
            if any(not isinstance(item, str) or not item for item in identifiers):
                return None
            now = int(self.clock())
            if value["exp"] <= now or value["exp"] > now + self.MAX_TTL_SECONDS:
                return None
            return WorkspaceToolClaims(
                user_id=value["sub"],
                run_id=value["run"],
                session_id=value["session"],
                attempt_id=value["attempt"],
                expires_at=value["exp"],
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None


def load_or_create_internal_tool_secret(path: str | PostgresDatabase) -> bytes:
    """All API instances using one agent database must verify the same Pi tool token."""
    if isinstance(path, PostgresDatabase):
        with path.transaction() as connection:
            connection.execute(
                "INSERT INTO internal_tool_auth (id,secret) VALUES (%s,%s) "
                "ON CONFLICT (id) DO NOTHING",
                ("hmac-sha256", secrets.token_bytes(32)),
            )
            secret = connection.execute(
                "SELECT secret FROM internal_tool_auth WHERE id='hmac-sha256'"
            ).fetchone()[0]
        if len(secret) != 32:
            raise ValueError("Invalid internal tool auth secret")
        return bytes(secret)
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
