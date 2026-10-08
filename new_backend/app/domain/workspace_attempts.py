"""Durable status and metering for workspace tool attempts."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.db.postgres import PostgresDatabase, PostgresStatements
from app.ports.workspace_sandbox import WorkspaceConflict

AttemptStatus = Literal[
    "queued", "running", "cancelling", "completed", "failed", "cancelled", "unknown"
]


class WorkspaceCpuQuotaExceeded(RuntimeError):
    """A command reservation exceeds the user's daily CPU allowance."""


@dataclass(frozen=True, slots=True)
class WorkspaceAttempt:
    attempt_id: str
    run_id: str
    session_id: str
    user_id: str
    status: AttemptStatus
    fencing_token: int
    provider_process_id: str | None
    reserved_cpu_core_ms: int
    wall_ms: int | None
    cpu_core_ms: int | None
    peak_memory_bytes: int | None
    exit_code: int | None
    output_truncated: bool
    provider_error_code: str | None
    started_at: float | None
    finished_at: float | None
    created_at: float
    updated_at: float


_COLUMNS = tuple(WorkspaceAttempt.__dataclass_fields__)


class WorkspaceAttemptStore:
    """Apply fenced, monotonic attempt transitions without storing output."""

    def __init__(
        self, database: PostgresDatabase, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.db = PostgresStatements(database)
        self.clock = clock

    def get(self, attempt_id: str) -> WorkspaceAttempt | None:
        row = self.db.execute(
            "SELECT " + ",".join(_COLUMNS) + " FROM workspace_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        return WorkspaceAttempt(*row) if row else None

    def create(
        self,
        *,
        attempt_id: str,
        run_id: str,
        session_id: str,
        user_id: str,
        fencing_token: int,
        reserved_cpu_core_ms: int,
    ) -> WorkspaceAttempt:
        if fencing_token <= 0 or reserved_cpu_core_ms < 0:
            raise ValueError("Workspace attempt reservation is invalid")
        now = self.clock()
        with self.db:
            self.db.execute(
                "INSERT INTO workspace_attempts (attempt_id,run_id,session_id,user_id,status,"
                "fencing_token,reserved_cpu_core_ms,created_at,updated_at) "
                "VALUES (?,?,?,?,'queued',?,?,?,?)",
                (
                    attempt_id,
                    run_id,
                    session_id,
                    user_id,
                    fencing_token,
                    reserved_cpu_core_ms,
                    now,
                    now,
                ),
            )
        return self._required(attempt_id)

    def reserve_cpu(
        self,
        attempt_id: str,
        fencing_token: int,
        requested_cpu_core_ms: int,
        default_daily_limit_ms: int,
    ) -> WorkspaceAttempt:
        """Atomically reserve one command budget against settled and active attempts."""
        if requested_cpu_core_ms <= 0 or default_daily_limit_ms < 0:
            raise ValueError("Workspace CPU reservation is invalid")
        now = datetime.now(UTC)
        day_start = datetime.combine(now.date(), datetime.min.time(), UTC).timestamp()
        day_end = (datetime.fromtimestamp(day_start, UTC) + timedelta(days=1)).timestamp()
        with self.db:
            current = self.db.execute(
                "SELECT " + ",".join(_COLUMNS) + " FROM workspace_attempts "
                "WHERE attempt_id=? FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            if current is None:
                raise WorkspaceConflict("Workspace attempt is unavailable")
            attempt = WorkspaceAttempt(*current)
            self.db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                (f"workspace-cpu:{attempt.user_id}",),
            )
            if attempt.fencing_token != fencing_token or attempt.status != "queued":
                raise WorkspaceConflict("Workspace CPU reservation is stale")
            if attempt.reserved_cpu_core_ms:
                if attempt.reserved_cpu_core_ms != requested_cpu_core_ms:
                    raise WorkspaceConflict("Workspace CPU reservation changed")
                return attempt
            explicit = self.db.execute(
                "SELECT limit_ms FROM compute_cpu_limits WHERE user_id=?",
                (attempt.user_id,),
            ).fetchone()
            limit = int(explicit[0]) if explicit else default_daily_limit_ms
            used = int(
                self.db.execute(
                    "SELECT COALESCE(SUM(cpu_core_ms),0) FROM workspace_attempts "
                    "WHERE user_id=? AND created_at>=? AND created_at<? "
                    "AND status IN ('completed','failed','cancelled')",
                    (attempt.user_id, day_start, day_end),
                ).fetchone()[0]
            )
            held = int(
                self.db.execute(
                    "SELECT COALESCE(SUM(reserved_cpu_core_ms),0) FROM workspace_attempts "
                    "WHERE user_id=? AND attempt_id<>? "
                    "AND status IN ('queued','running','cancelling','unknown')",
                    (attempt.user_id, attempt_id),
                ).fetchone()[0]
            )
            if requested_cpu_core_ms > max(0, limit - used - held):
                raise WorkspaceCpuQuotaExceeded
            self.db.execute(
                "UPDATE workspace_attempts SET reserved_cpu_core_ms=?,updated_at=? "
                "WHERE attempt_id=?",
                (requested_cpu_core_ms, self.clock(), attempt_id),
            )
        return self._required(attempt_id)

    def mark_running(
        self, attempt_id: str, fencing_token: int, provider_process_id: str
    ) -> WorkspaceAttempt:
        return self._transition(
            attempt_id,
            fencing_token,
            expected={"queued"},
            status="running",
            assignments={"provider_process_id": provider_process_id, "started_at": self.clock()},
        )

    def begin_cancel(self, attempt_id: str, fencing_token: int) -> WorkspaceAttempt:
        current = self._required(attempt_id)
        if current.status == "cancelling":
            return current
        return self._transition(
            attempt_id,
            fencing_token,
            expected={"queued", "running"},
            status="cancelling",
        )

    def finish(
        self,
        attempt_id: str,
        fencing_token: int,
        *,
        status: Literal["completed", "failed", "cancelled"],
        wall_ms: int,
        cpu_core_ms: int | None,
        peak_memory_bytes: int | None,
        exit_code: int | None,
        output_truncated: bool,
        provider_error_code: str | None = None,
    ) -> WorkspaceAttempt:
        values = (wall_ms, cpu_core_ms, peak_memory_bytes)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("Workspace attempt usage cannot be negative")
        return self._transition(
            attempt_id,
            fencing_token,
            expected={"queued", "running", "cancelling", "unknown"},
            status=status,
            assignments={
                "wall_ms": wall_ms,
                "cpu_core_ms": cpu_core_ms,
                "peak_memory_bytes": peak_memory_bytes,
                "exit_code": exit_code,
                "output_truncated": output_truncated,
                "provider_error_code": provider_error_code,
                "finished_at": self.clock(),
            },
        )

    def mark_unknown(
        self, attempt_id: str, fencing_token: int, provider_error_code: str
    ) -> WorkspaceAttempt:
        return self._transition(
            attempt_id,
            fencing_token,
            expected={"queued", "running", "cancelling"},
            status="unknown",
            assignments={"provider_error_code": provider_error_code},
        )

    def _transition(
        self,
        attempt_id: str,
        fencing_token: int,
        *,
        expected: set[str],
        status: AttemptStatus,
        assignments: dict[str, object] | None = None,
    ) -> WorkspaceAttempt:
        values = dict(assignments or {})
        values["status"] = status
        values["updated_at"] = self.clock()
        with self.db:
            self.db.execute(
                "SELECT attempt_id FROM workspace_attempts WHERE attempt_id=? FOR UPDATE",
                (attempt_id,),
            )
            current = self._required(attempt_id)
            if current.fencing_token != fencing_token or current.status not in expected:
                raise WorkspaceConflict("Workspace attempt transition is stale")
            keys = tuple(values)
            self.db.execute(
                "UPDATE workspace_attempts SET "
                + ",".join(f"{key}=?" for key in keys)
                + " WHERE attempt_id=?",
                tuple(values[key] for key in keys) + (attempt_id,),
            )
        return self._required(attempt_id)

    def _required(self, attempt_id: str) -> WorkspaceAttempt:
        record = self.get(attempt_id)
        if record is None:
            raise WorkspaceConflict("Workspace attempt is unavailable")
        return record
