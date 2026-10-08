"""Durable OpenSandbox ownership, creation fencing and attempt leases."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from app.db.postgres import PostgresDatabase, PostgresStatements
from app.ports.workspace_sandbox import WorkspaceConflict

LifecycleState = Literal["creating", "ready", "draining", "replacing", "error"]
RuntimeState = Literal["pending", "running", "stopped", "unknown"]
LeaseState = Literal["active", "unknown", "released"]
TerminalProcessState = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class WorkspaceSandboxRecord:
    """Persistent identity for a replaceable sandbox and durable volume."""

    user_id: str
    provider: str
    sandbox_id: str | None
    volume_id: str
    image_digest: str
    provider_revision: int
    lifecycle_state: LifecycleState
    runtime_state: RuntimeState
    claim_token: str | None
    claim_expires_at: float | None
    last_error_code: str | None
    last_confirmed_at: float
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class WorkspaceCreationClaim:
    """Outcome of an idempotent create claim."""

    record: WorkspaceSandboxRecord
    claimed: bool


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseRecord:
    """One serialized Session attempt with an explicit fencing token."""

    attempt_id: str
    user_id: str
    session_id: str
    run_id: str
    fencing_token: int
    state: LeaseState
    expires_at: float
    process_id: str | None
    process_state: str
    exit_code: int | None
    exit_confirmed: bool
    created_at: float
    updated_at: float


_SANDBOX_COLUMNS = (
    "user_id", "provider", "sandbox_id", "volume_id", "image_digest",
    "provider_revision", "lifecycle_state", "runtime_state", "claim_token",
    "claim_expires_at", "last_error_code", "last_confirmed_at", "created_at", "updated_at",
)
_LEASE_COLUMNS = (
    "attempt_id", "user_id", "session_id", "run_id", "fencing_token", "state",
    "expires_at", "process_id", "process_state", "exit_code", "exit_confirmed",
    "created_at", "updated_at",
)


class WorkspaceSandboxStore:
    """Serialize lifecycle transitions in PostgreSQL without deleting volumes."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        clock: Callable[[], float] = time.time,
        claim_seconds: float = 60.0,
    ) -> None:
        if claim_seconds <= 0:
            raise ValueError("Workspace claim duration must be positive")
        self.db = PostgresStatements(database)
        self.clock = clock
        self.claim_seconds = claim_seconds

    @contextmanager
    def _locked_user(self, user_id: str):
        with self.db:
            # A row lock cannot serialize the first insert because no row exists yet.
            # The transaction-scoped advisory lock closes that creation race.
            self.db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                (f"workspace-sandbox:{user_id}",),
            )
            self.db.execute(
                "SELECT user_id FROM workspace_sandboxes WHERE user_id=? FOR UPDATE",
                (user_id,),
            )
            yield

    def get(self, user_id: str) -> WorkspaceSandboxRecord | None:
        row = self.db.execute(
            "SELECT " + ",".join(_SANDBOX_COLUMNS)
            + " FROM workspace_sandboxes WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return WorkspaceSandboxRecord(*row) if row else None

    def list(self) -> list[WorkspaceSandboxRecord]:
        return [
            WorkspaceSandboxRecord(*row)
            for row in self.db.execute(
                "SELECT " + ",".join(_SANDBOX_COLUMNS)
                + " FROM workspace_sandboxes ORDER BY user_id"
            )
        ]

    def claim_creation(
        self,
        user_id: str,
        *,
        provider: str,
        image_digest: str,
    ) -> WorkspaceCreationClaim:
        """Claim the one permitted initial instance, recovering stale claims."""
        if provider != "opensandbox":
            raise ValueError("Unsupported workspace provider")
        now = self.clock()
        with self._locked_user(user_id):
            current = self.get(user_id)
            if current is None:
                token = "claim-" + uuid.uuid4().hex
                volume_id = "workspace-volume-" + uuid.uuid4().hex
                self.db.execute(
                    "INSERT INTO workspace_sandboxes ("
                    + ",".join(_SANDBOX_COLUMNS)
                    + ") VALUES (?,?,?,?,?,1,'creating','pending',?,?,NULL,?,?,?)",
                    (
                        user_id, provider, None, volume_id, image_digest,
                        token, now + self.claim_seconds, 0.0, now, now,
                    ),
                )
                return WorkspaceCreationClaim(self._required_record(user_id), True)
            if current.provider != provider or current.image_digest != image_digest:
                raise WorkspaceConflict("Workspace provider or image differs from its owner record")
            if current.lifecycle_state == "creating":
                if current.claim_expires_at is not None and current.claim_expires_at > now:
                    return WorkspaceCreationClaim(current, False)
                token = "claim-" + uuid.uuid4().hex
                self.db.execute(
                    "UPDATE workspace_sandboxes SET claim_token=?,claim_expires_at=?,"
                    "provider_revision=provider_revision+1,updated_at=? WHERE user_id=?",
                    (token, now + self.claim_seconds, now, user_id),
                )
                return WorkspaceCreationClaim(self._required_record(user_id), True)
            if current.lifecycle_state == "error" and current.sandbox_id is None:
                token = "claim-" + uuid.uuid4().hex
                self.db.execute(
                    "UPDATE workspace_sandboxes SET lifecycle_state='creating',"
                    "runtime_state='pending',claim_token=?,claim_expires_at=?,"
                    "last_error_code=NULL,provider_revision=provider_revision+1,updated_at=? "
                    "WHERE user_id=?",
                    (token, now + self.claim_seconds, now, user_id),
                )
                return WorkspaceCreationClaim(self._required_record(user_id), True)
            return WorkspaceCreationClaim(current, False)

    def confirm_instance(
        self,
        user_id: str,
        *,
        claim_token: str,
        sandbox_id: str,
        runtime_state: Literal["running", "stopped"] = "running",
    ) -> WorkspaceSandboxRecord:
        now = self.clock()
        with self._locked_user(user_id):
            current = self.get(user_id)
            if (
                current is None
                or current.lifecycle_state not in {"creating", "replacing"}
                or current.claim_token != claim_token
                or current.claim_expires_at is None
                or current.claim_expires_at <= now
            ):
                raise WorkspaceConflict("Workspace creation claim is stale")
            self.db.execute(
                "UPDATE workspace_sandboxes SET sandbox_id=?,lifecycle_state='ready',"
                "runtime_state=?,claim_token=NULL,claim_expires_at=NULL,last_error_code=NULL,"
                "provider_revision=provider_revision+1,last_confirmed_at=?,updated_at=? "
                "WHERE user_id=?",
                (sandbox_id, runtime_state, now, now, user_id),
            )
            return self._required_record(user_id)

    def mark_error(self, user_id: str, claim_token: str, error_code: str) -> None:
        """Close a claimed transition without exposing provider error text."""
        now = self.clock()
        with self._locked_user(user_id):
            current = self.get(user_id)
            if current is None or current.claim_token != claim_token:
                raise WorkspaceConflict("Workspace error claim is stale")
            self.db.execute(
                "UPDATE workspace_sandboxes SET lifecycle_state='error',"
                "runtime_state='unknown',claim_token=NULL,claim_expires_at=NULL,"
                "last_error_code=?,provider_revision=provider_revision+1,updated_at=? "
                "WHERE user_id=?",
                (error_code, now, user_id),
            )

    def mark_runtime(
        self, user_id: str, runtime_state: RuntimeState,
    ) -> WorkspaceSandboxRecord:
        if runtime_state == "pending":
            raise ValueError("Pending runtime is reserved for lifecycle claims")
        now = self.clock()
        with self._locked_user(user_id):
            current = self.get(user_id)
            if current is None or current.lifecycle_state != "ready":
                raise WorkspaceConflict("Workspace is not ready")
            self.db.execute(
                "UPDATE workspace_sandboxes SET runtime_state=?,last_confirmed_at=?,updated_at=? "
                "WHERE user_id=?",
                (runtime_state, now, now, user_id),
            )
            return self._required_record(user_id)

    def acquire_lease(
        self,
        *,
        attempt_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        lease_seconds: float,
    ) -> WorkspaceLeaseRecord:
        if lease_seconds <= 0:
            raise ValueError("Workspace lease duration must be positive")
        now = self.clock()
        with self._locked_user(user_id):
            owner = self.get(user_id)
            if (
                owner is None
                or owner.lifecycle_state != "ready"
                or owner.runtime_state != "running"
            ):
                raise WorkspaceConflict("Workspace is not accepting attempts")
            existing = self._lease(attempt_id)
            if existing is not None:
                if (
                    existing.user_id == user_id
                    and existing.session_id == session_id
                    and existing.run_id == run_id
                ):
                    return existing
                raise WorkspaceConflict("Attempt ID is already assigned")
            unresolved = self.db.execute(
                "SELECT attempt_id FROM workspace_sandbox_leases "
                "WHERE user_id=? AND session_id=? AND state<>'released'",
                (user_id, session_id),
            ).fetchone()
            if unresolved:
                raise WorkspaceConflict("Session already has an unresolved workspace attempt")
            fencing = self.db.execute(
                "SELECT COALESCE(MAX(fencing_token),0)+1 FROM workspace_sandbox_leases "
                "WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO workspace_sandbox_leases ("
                + ",".join(_LEASE_COLUMNS)
                + ") VALUES (?,?,?,?,?,'active',?,NULL,'pending',NULL,false,?,?)",
                (
                    attempt_id, user_id, session_id, run_id, fencing,
                    now + lease_seconds, now, now,
                ),
            )
            return self._lease(attempt_id)

    def attach_process(
        self, attempt_id: str, fencing_token: int, process_id: str,
    ) -> WorkspaceLeaseRecord:
        lease = self._required_lease(attempt_id)
        with self._locked_user(lease.user_id):
            lease = self._required_lease(attempt_id)
            self._require_active_fence(lease, fencing_token)
            if lease.process_id not in {None, process_id}:
                raise WorkspaceConflict("Workspace process is already attached")
            self.db.execute(
                "UPDATE workspace_sandbox_leases SET process_id=?,process_state='running',"
                "updated_at=? WHERE attempt_id=?",
                (process_id, self.clock(), attempt_id),
            )
            return self._required_lease(attempt_id)

    def renew_lease(
        self, attempt_id: str, fencing_token: int, lease_seconds: float,
    ) -> WorkspaceLeaseRecord:
        if lease_seconds <= 0:
            raise ValueError("Workspace lease duration must be positive")
        lease = self._required_lease(attempt_id)
        with self._locked_user(lease.user_id):
            lease = self._required_lease(attempt_id)
            self._require_active_fence(lease, fencing_token)
            self.db.execute(
                "UPDATE workspace_sandbox_leases SET state='active',expires_at=?,updated_at=? "
                "WHERE attempt_id=?",
                (self.clock() + lease_seconds, self.clock(), attempt_id),
            )
            return self._required_lease(attempt_id)

    def mark_expired_unknown(self) -> int:
        """Fence expired work without claiming that its process exited."""
        now = self.clock()
        with self.db:
            result = self.db.execute(
                "UPDATE workspace_sandbox_leases SET state='unknown',"
                "process_state='unknown',updated_at=? "
                "WHERE state='active' AND expires_at<=?",
                (now, now),
            )
        return result.rowcount

    def release_after_exit(
        self,
        attempt_id: str,
        fencing_token: int,
        *,
        terminal_state: TerminalProcessState,
        exit_code: int | None,
    ) -> WorkspaceLeaseRecord:
        lease = self._required_lease(attempt_id)
        with self._locked_user(lease.user_id):
            lease = self._required_lease(attempt_id)
            if lease.fencing_token != fencing_token or lease.state == "released":
                raise WorkspaceConflict("Workspace lease fencing token is stale")
            self.db.execute(
                "UPDATE workspace_sandbox_leases SET state='released',process_state=?,"
                "exit_code=?,exit_confirmed=true,updated_at=? WHERE attempt_id=?",
                (terminal_state, exit_code, self.clock(), attempt_id),
            )
            return self._required_lease(attempt_id)

    def begin_replace(
        self, user_id: str, expected_revision: int,
    ) -> WorkspaceSandboxRecord:
        now = self.clock()
        with self._locked_user(user_id):
            current = self.get(user_id)
            if current is None or current.provider_revision != expected_revision:
                raise WorkspaceConflict("Workspace revision is stale")
            unresolved = self.db.execute(
                "SELECT 1 FROM workspace_sandbox_leases WHERE user_id=? AND state<>'released' "
                "LIMIT 1",
                (user_id,),
            ).fetchone()
            if unresolved:
                raise WorkspaceConflict("Workspace has unresolved attempts")
            if current.lifecycle_state not in {"ready", "error"}:
                raise WorkspaceConflict("Workspace cannot be replaced in its current state")
            token = "replace-" + uuid.uuid4().hex
            self.db.execute(
                "UPDATE workspace_sandboxes SET lifecycle_state='replacing',"
                "runtime_state='pending',claim_token=?,claim_expires_at=?,"
                "last_error_code=NULL,provider_revision=provider_revision+1,updated_at=? "
                "WHERE user_id=?",
                (token, now + self.claim_seconds, now, user_id),
            )
            return self._required_record(user_id)

    def confirm_replace(
        self,
        user_id: str,
        *,
        claim_token: str,
        sandbox_id: str,
    ) -> WorkspaceSandboxRecord:
        """Attach a replacement instance while retaining the existing volume ID."""
        return self.confirm_instance(
            user_id,
            claim_token=claim_token,
            sandbox_id=sandbox_id,
            runtime_state="running",
        )

    def leases_for_user(self, user_id: str) -> list[WorkspaceLeaseRecord]:
        return [
            WorkspaceLeaseRecord(*row)
            for row in self.db.execute(
                "SELECT " + ",".join(_LEASE_COLUMNS)
                + " FROM workspace_sandbox_leases WHERE user_id=? ORDER BY created_at",
                (user_id,),
            )
        ]

    def _lease(self, attempt_id: str) -> WorkspaceLeaseRecord | None:
        row = self.db.execute(
            "SELECT " + ",".join(_LEASE_COLUMNS)
            + " FROM workspace_sandbox_leases WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        return WorkspaceLeaseRecord(*row) if row else None

    def _required_record(self, user_id: str) -> WorkspaceSandboxRecord:
        record = self.get(user_id)
        if record is None:
            raise WorkspaceConflict("Workspace owner record is unavailable")
        return record

    def _required_lease(self, attempt_id: str) -> WorkspaceLeaseRecord:
        lease = self._lease(attempt_id)
        if lease is None:
            raise WorkspaceConflict("Workspace lease is unavailable")
        return lease

    @staticmethod
    def _require_active_fence(lease: WorkspaceLeaseRecord, fencing_token: int) -> None:
        if lease.fencing_token != fencing_token or lease.state == "released":
            raise WorkspaceConflict("Workspace lease fencing token is stale")
