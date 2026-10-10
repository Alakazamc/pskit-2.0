"""Server-owned roles and the transaction boundary shared by management services."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from app.contracts.admin import AdminMe, AuditEvent
from app.db.postgres import PostgresDatabase, PostgresStatements

READ_PERMISSIONS = {
    "models:read",
    "services:read",
    "quotas:read",
    "jobs:read",
    "sandboxes:read",
    "usage:read",
    "audit:read",
}
ROLE_PERMISSIONS = {
    "platform_admin": READ_PERMISSIONS
    | {
        "models:write",
        "models:publish",
        "services:write",
        "services:publish",
        "quotas:write",
        "jobs:cancel",
        "sandboxes:drain",
        "usage:reconcile",
    },
    "service_maintainer": {"models:read", "services:read", "services:write", "jobs:read"},
    "quota_operator": {
        "quotas:read",
        "quotas:write",
        "jobs:read",
        "jobs:cancel",
        "sandboxes:read",
        "sandboxes:drain",
        "usage:read",
        "usage:reconcile",
    },
    "auditor": READ_PERMISSIONS,
}


class RevisionConflict(ValueError):
    """The submitted revision no longer describes the resource."""


class AdminStore:
    """Persist administrator roles and mutations in the private business database."""

    def __init__(self, storage: str | PostgresDatabase, *, identity_policy=None, quotas=None):
        self.database = storage if isinstance(storage, PostgresDatabase) else None
        self.identity_policy = identity_policy
        self.quotas = quotas
        self.lock = RLock()
        if self.database:
            self.db = PostgresStatements(self.database)
        else:
            if storage != ":memory:":
                Path(storage).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(storage, check_same_thread=False)
            migration = Path(__file__).resolve().parents[2] / "db/postgres_migrations/006_admin.sql"
            self.db.executescript(
                migration.read_text()
                .split("-- PostgreSQL job revisions")[0]
                .replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
                .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
            )

    @contextmanager
    def transaction(self):
        """Use one connection for revision, business update, audit and outbox."""
        with self.lock, self.db:
            if self.database:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission',0))"
                )
            yield self.db

    def principal(self, user_id: str) -> AdminMe:
        """Read current grants every time; identity claims cannot add permissions."""
        rows = self.db.execute(
            "SELECT role,service_id FROM admin_roles WHERE user_id=?", (user_id,)
        ).fetchall()
        roles = sorted({row[0] for row in rows})
        return AdminMe(
            user_id=user_id,
            roles=roles,
            permissions=sorted(set().union(*(ROLE_PERMISSIONS[r] for r in roles))),
            service_ids=sorted({row[1] for row in rows if row[1]}),
        )

    def grant(self, user_id: str, role: str, *, service_id: str = "", actor: str, reason: str):
        """Bootstrap a server grant with a recorded operator source."""
        if role not in ROLE_PERMISSIONS or (role == "service_maintainer" and not service_id):
            raise ValueError("INVALID_ADMIN_ROLE")
        if len(reason.strip()) < 5:
            raise ValueError("REASON_REQUIRED")
        with self.transaction():
            before = self.principal(user_id).model_dump()
            self.db.execute(
                "INSERT INTO admin_roles(user_id,role,service_id) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (user_id, role, service_id),
            )
            self.audit(
                actor, "roles:grant", user_id, reason, before, self.principal(user_id).model_dump()
            )
        return self.principal(user_id)

    def revoke(
        self, user_id: str, role: str, *, service_id: str | None = None, actor: str, reason: str
    ):
        """Revoke a current role without waiting for a token to expire."""
        if len(reason.strip()) < 5:
            raise ValueError("REASON_REQUIRED")
        with self.transaction():
            before = self.principal(user_id).model_dump()
            if service_id is None:
                self.db.execute(
                    "DELETE FROM admin_roles WHERE user_id=? AND role=?", (user_id, role)
                )
            else:
                self.db.execute(
                    "DELETE FROM admin_roles WHERE user_id=? AND role=? AND service_id=?",
                    (user_id, role, service_id),
                )
            self.audit(
                actor, "roles:revoke", user_id, reason, before, self.principal(user_id).model_dump()
            )
        return self.principal(user_id)

    def audit(
        self, actor, action, resource_id, reason, before, after, request_id=None, *, connection=None
    ):
        """Append an audit in the caller's transaction; failure aborts all writes."""
        event = AuditEvent(
            event_id=uuid.uuid4().hex,
            actor_user_id=actor,
            action=action,
            resource_id=resource_id,
            reason=reason,
            request_id=request_id or uuid.uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            before=before,
            after=after,
        )
        (connection if connection is not None else self.db).execute(
            "INSERT INTO admin_audit_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                actor,
                action,
                resource_id,
                reason,
                event.request_id,
                event.created_at,
                json.dumps(before),
                json.dumps(after),
            ),
        )
        return event

    def revision(self, kind, resource_id):
        row = self.db.execute(
            "SELECT revision FROM admin_revisions WHERE kind=? AND resource_id=?",
            (kind, resource_id),
        ).fetchone()
        return row[0] if row else 0

    def advance(self, kind, resource_id, expected):
        current = self.revision(kind, resource_id)
        if current != expected:
            raise RevisionConflict("REVISION_CONFLICT")
        self.db.execute(
            "INSERT INTO admin_revisions VALUES (?,?,?) ON CONFLICT(kind,resource_id) DO UPDATE SET revision=excluded.revision",
            (kind, resource_id, current + 1),
        )
        return current + 1

    def audit_events(self, *, limit=50, cursor=None):
        """Page audit records without exposing credential contents."""
        rows = self.db.execute(
            "SELECT event_id,actor_user_id,action,resource_id,reason,request_id,created_at,before_json,after_json FROM admin_audit_events WHERE event_id>? ORDER BY event_id LIMIT ?",
            (cursor or "", limit + 1),
        ).fetchall()
        items = [
            AuditEvent(
                event_id=r[0],
                actor_user_id=r[1],
                action=r[2],
                resource_id=r[3],
                reason=r[4],
                request_id=r[5],
                created_at=r[6],
                before=json.loads(r[7]),
                after=json.loads(r[8]),
            )
            for r in rows[:limit]
        ]
        return {"items": items, "next_cursor": items[-1].event_id if len(rows) > limit else None}

    def concurrency_limit_for(self, user_id):
        """Return an explicit administrator cap; absent policy preserves runtime defaults."""
        row = self.db.execute(
            "SELECT concurrency_limit FROM admin_user_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        return row[0] if row else None

    def storage_limit_for(self, user_id):
        """Return an explicit byte cap, including zero; absence keeps current limits."""
        row = self.db.execute(
            "SELECT storage_limit_bytes FROM admin_user_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        return row[0] if row else None
