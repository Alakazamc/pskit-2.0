"""Database-enforced release admission with durable, explicitly released ownership."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus

from app.db.postgres import PostgresDatabase, PostgresStatements

ENFORCEMENT_VERSION = "pskit.db-admission.v1"
COMPONENT_VERSION = 1
SCOPES = ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"]
IDENTITY_FIELDS = {
    "adapter_id", "qualification_sha256", "owner_nonce", "environment", "project",
    "previous_manifest_sha256", "target_manifest_sha256", "scopes",
}
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission',0))"
_STATE_COLUMNS = (
    "component_version,deployment_id,environment,project,qualification_sha256,state,"
    "identity_json,generation,updated_at"
)


class AdmissionClosed(RuntimeError):
    """New work is temporarily unavailable; no quota has been reserved."""


class AdmissionConflict(RuntimeError):
    """A private owner or deployment binding is invalid; keep any hold closed."""


class AdmissionUnavailable(AdmissionClosed):
    """Admission state cannot be verified; deny new work."""


def validate_identity(identity: dict) -> dict:
    """Accept exactly the controller's fixed identity and complete execution scope."""
    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS:
        raise ValueError("Admission identity fields are invalid")
    if (not isinstance(identity["adapter_id"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", identity["adapter_id"]) is None):
        raise ValueError("Admission adapter identity is invalid")
    for key in ("qualification_sha256", "owner_nonce", "previous_manifest_sha256", "target_manifest_sha256"):
        if not isinstance(identity[key], str) or re.fullmatch(r"[0-9a-f]{64}", identity[key]) is None:
            raise ValueError("Admission identity digest is invalid")
    if (identity["environment"], identity["project"]) not in {
        ("prod", "pskit-agent-cloud"), ("staging", "pskit-agent-staging"),
    } or identity["scopes"] != SCOPES or not isinstance(identity["scopes"], list):
        raise ValueError("Admission deployment or execution scope is invalid")
    return json.loads(json.dumps(identity, sort_keys=True))


def transaction_lock(connection) -> None:
    """Keep admission's linearization lock until the whole transaction commits."""
    if isinstance(connection, sqlite3.Connection):
        if not connection.in_transaction:
            raise AdmissionUnavailable("Admission requires a write transaction")
        return
    if isinstance(connection, PostgresStatements):
        if not getattr(connection._local, "stack", ()):
            raise AdmissionUnavailable("Admission requires a write transaction")
    elif connection.info.transaction_status != TransactionStatus.INTRANS:
        raise AdmissionUnavailable("Admission requires a write transaction")
    connection.execute(_LOCK_SQL)


class DurableAdmissionGate:
    """One deployment binding and owner held in the existing persistent database."""

    def __init__(self, source, *, environment, project, qualification_sha256, clock=time.time):
        self.db = PostgresStatements(source) if isinstance(source, PostgresDatabase) else source
        self.environment, self.project, self.qualification_sha256 = environment, project, qualification_sha256
        self.clock = clock
        validate_identity({"adapter_id": "binding", "owner_nonce": "0" * 64,
                           "previous_manifest_sha256": "0" * 64, "target_manifest_sha256": "0" * 64,
                           "environment": environment, "project": project,
                           "qualification_sha256": qualification_sha256, "scopes": SCOPES})

    @contextmanager
    def _transaction(self):
        if (isinstance(self.db, sqlite3.Connection) and self.db.in_transaction
                or isinstance(self.db, PostgresStatements) and getattr(self.db._local, "stack", ())):
            raise AdmissionUnavailable("An admission acknowledgement requires its own transaction")
        if isinstance(self.db, sqlite3.Connection):
            self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            transaction_lock(self.db)
            yield

    def _row(self, connection):
        try:
            versions = [row[0] for row in connection.execute(
                "SELECT version FROM release_admission_migrations ORDER BY version",
            )]
            # Under REPEATABLE READ, the advisory-lock wait itself can establish
            # an older snapshot. The current-row lock forces a serialization
            # error instead of accepting an obsolete OPEN state after closure.
            current_row = "" if isinstance(connection, sqlite3.Connection) else " FOR UPDATE"
            row = connection.execute(
                "SELECT " + _STATE_COLUMNS + " FROM release_admission_state WHERE singleton=1" + current_row,
            ).fetchone()
        except (psycopg.Error, sqlite3.Error):
            raise AdmissionUnavailable("Admission component is unavailable") from None
        if versions != [COMPONENT_VERSION] or row is None or row[0] != COMPONENT_VERSION:
            raise AdmissionUnavailable("Admission component version is unavailable")
        if (not isinstance(row[1], str) or re.fullmatch(r"[0-9a-f]{32}", row[1]) is None
                or row[5] not in {"open", "closed"} or type(row[7]) is not int or row[7] < 0):
            raise AdmissionUnavailable("Admission component state is invalid")
        return row

    def _bound(self, connection):
        row = self._row(connection)
        if row[2:5] != (self.environment, self.project, self.qualification_sha256):
            raise AdmissionUnavailable("Admission deployment binding differs")
        return row

    def configure(self) -> None:
        """Bind an explicitly installed open component; never install DDL or reopen it."""
        with self._transaction():
            row = self._row(self.db)
            if row[2:5] == (None, None, None) and row[5] == "open" and row[6] is None:
                placeholders = "?,?,?" if isinstance(self.db, sqlite3.Connection) else "%s,%s,%s"
                values = placeholders.split(",")
                self.db.execute(
                    f"UPDATE release_admission_state SET environment={values[0]},project={values[1]},"
                    f"qualification_sha256={values[2]} WHERE singleton=1",
                    (self.environment, self.project, self.qualification_sha256),
                )
            self._bound(self.db)

    def require_open(self, connection) -> None:
        transaction_lock(connection)
        if self._bound(connection)[5] != "open":
            raise AdmissionClosed("Release admission is closed")

    def allows(self, connection) -> bool:
        try:
            self.require_open(connection)
        except AdmissionClosed:
            return False
        return True

    def _operation(self, operation, identity):
        identity = validate_identity(identity)
        if (identity["environment"], identity["project"], identity["qualification_sha256"]) != (
            self.environment, self.project, self.qualification_sha256,
        ):
            raise AdmissionConflict("Admission owner binding differs")
        with self._transaction():
            row = self._bound(self.db)
            observed = self._time()
            saved = json.loads(row[6]) if row[6] is not None else None
            if row[5] == "closed" and saved != identity or operation != "enter" and saved != identity:
                raise AdmissionConflict("Admission owner does not match")
            if operation == "verify" and row[5] != "closed":
                raise AdmissionConflict("Admission hold is not closed")
            target = "open" if operation == "release" else "closed"
            if row[5] != target:
                placeholder = "?" if isinstance(self.db, sqlite3.Connection) else "%s"
                self.db.execute(
                    f"UPDATE release_admission_state SET state={placeholder},identity_json={placeholder},"
                    f"updated_at={placeholder},generation=generation+1 WHERE singleton=1",
                    (target, json.dumps(identity, sort_keys=True), observed),
                )
        return {**identity, "schema_version": "pskit.admission-receipt.v1",
                "state": "released" if operation == "release" else "closed",
                "hold_mode": "until_explicit_release", "observed_at": observed,
                "valid_until": observed + 20}

    def enter(self, identity):
        return self._operation("enter", identity)

    def verify(self, identity):
        return self._operation("verify", identity)

    def release(self, identity):
        return self._operation("release", identity)

    def observe(self, challenge):
        if not isinstance(challenge, str) or re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
            raise ValueError("Admission observation challenge is invalid")
        with self._transaction():
            row = self._bound(self.db)
            observed = self._time()
        return {"schema_version": "pskit.admission-observation.v1", "challenge": challenge,
                "enabled": True, "enforcement_version": ENFORCEMENT_VERSION,
                "component_version": COMPONENT_VERSION, "qualification_sha256": row[4],
                "environment": row[2], "project": row[3], "deployment_id": row[1],
                "state": row[5], "identity": json.loads(row[6]) if row[6] else None,
                "generation": row[7], "observed_at": observed, "scopes": SCOPES}

    def _time(self):
        observed = self.clock()
        if type(observed) not in {int, float} or not math.isfinite(observed) or observed < 0:
            raise AdmissionUnavailable("Admission clock is unavailable")
        return observed
