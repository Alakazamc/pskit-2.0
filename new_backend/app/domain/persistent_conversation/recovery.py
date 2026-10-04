"""Recovery persistence methods for the conversation store."""

import json
from datetime import datetime, timedelta

from app.contracts.conversation import (
    ArtifactCreatedData,
    ArtifactCreatedEvent,
    RunFailedData,
    RunFailedEvent,
    RunRetryingData,
    RunRetryingEvent,
    TaskUpdatedData,
    TaskUpdatedEvent,
    UsageUpdatedData,
    UsageUpdatedEvent,
)

from .common import current_time as _now


class RecoveryMixin:
    """Advance mock jobs and recover interrupted durable Pi Runs."""

    def advance_mock_jobs(self, duration_seconds: float) -> None:
        """Advance queued and running mock AF3 jobs from elapsed time.

        This writes task, usage, and artifact events when the simulated job
        changes state; production compute jobs use their worker callbacks.

        Args:
            duration_seconds: Simulated total runtime, split between queue
                and running phases.
        """

        rows = self.db.execute(
            "SELECT id,user_id,run_id,status,progress,estimated_minutes,created_at FROM agent_jobs "
            "WHERE json_extract(resource_requirements_json, '$.capability')='af3' "
            "AND status IN ('queued','running')"
        ).fetchall()
        for job_id, user_id, run_id, status, progress, estimate, created_at in rows:
            elapsed = (_now() - datetime.fromisoformat(created_at)).total_seconds()
            if status == "queued" and elapsed >= duration_seconds / 2:
                with self.db:
                    changed = self.db.execute(
                        "UPDATE agent_jobs SET status='running',progress=50 WHERE id=? AND status='queued'",
                        (job_id,),
                    ).rowcount
                if changed and run_id:
                    self.append_event(
                        user_id, run_id,
                        TaskUpdatedEvent(
                            run_id=run_id,
                            data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3", status="running", progress=50),
                        ),
                    )
            elif status == "running" and elapsed >= duration_seconds:
                actual = min(18, estimate)
                artifact = {"id": f"artifact-{job_id}", "name": "af3_prediction.cif", "kind": "structure"}
                with self.db:
                    changed = self.db.execute(
                        "UPDATE agent_jobs SET status='completed',progress=100,actual_minutes=?,"
                        "artifacts=?,simulation=1,gpu_accounting_status='settled' "
                        "WHERE id=? AND status='running'",
                        (actual, json.dumps([artifact]), job_id),
                    ).rowcount
                if changed and run_id:
                    self.append_event(
                        user_id, run_id,
                        TaskUpdatedEvent(
                            run_id=run_id,
                            data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3", status="completed", progress=100),
                        ),
                    )
                    self.append_event(
                        user_id, run_id,
                        UsageUpdatedEvent(
                            run_id=run_id,
                            data=UsageUpdatedData(gpu_remaining=self.usage_for(user_id).gpu.remaining),
                        ),
                    )
                    self.append_event(
                        user_id, run_id,
                        ArtifactCreatedEvent(
                            run_id=run_id,
                            data=ArtifactCreatedData(
                                artifact_id=artifact["id"], name=artifact["name"], kind=artifact["kind"]
                            ),
                        ),
                    )

    def claim_wakeups(
        self, owner: str = "local", lease_seconds: int = 10,
        *, max_active: int = 4, max_user_active: int = 2,
    ) -> list[tuple[str, str, str, str]]:
        """Lease eligible completed jobs whose waiting Pi Runs can resume.

        The claim is atomic per Run and respects global and per-user limits.
        Pending approvals and unfinished jobs prevent a claim.

        Args:
            owner: Worker identity stored on claimed Run leases.
            lease_seconds: Duration of each resume lease.
            max_active: Global active Run limit.
            max_user_active: Default active Run limit per user.

        Returns:
            Tuples of Run ID, user ID, session ID, and completed job ID.
        """
        rows = self.db.execute(
            "SELECT r.id,r.user_id,r.session_id,j.id FROM agent_runs r JOIN agent_jobs j ON j.run_id=r.id "
            "WHERE r.status='waiting' AND j.status IN ('completed','failed','cancelled') AND r.resume_attempts<3 "
            "AND (r.last_resumed_job_id IS NULL OR j.id<>r.last_resumed_job_id) "
            "AND NOT EXISTS (SELECT 1 FROM agent_approvals a WHERE a.run_id=r.id "
            "AND a.status='pending') "
            "AND (r.retry_after IS NULL OR r.retry_after<=?) "
            "AND NOT EXISTS (SELECT 1 FROM agent_jobs pending WHERE pending.run_id=r.id "
            "AND pending.status IN ('queued','running','cancelling')) ORDER BY j.created_at DESC",
            (_now().isoformat(),),
        ).fetchall()
        claimed: list[tuple[str, str, str, str]] = []
        for run_id, user_id, session_id, job_id in rows:
            with self._immediate_transaction():
                now = _now().isoformat()
                active = self.db.execute(
                    "SELECT COUNT(*) FROM agent_runs WHERE status IN ('running','resume_queued') "
                    "AND lease_expires_at>?", (now,),
                ).fetchone()[0]
                user_active = self.db.execute(
                    "SELECT COUNT(*) FROM agent_runs WHERE user_id=? "
                    "AND status IN ('running','resume_queued') AND lease_expires_at>?",
                    (user_id, now),
                ).fetchone()[0]
                if active >= max_active or user_active >= self._max_active_for_user(
                    user_id, max_user_active,
                ):
                    continue
                changed = self.db.execute(
                    "UPDATE agent_runs SET status='resume_queued',active_resume_job_id=?,"
                    "lease_owner=?,lease_expires_at=? "
                    "WHERE id=? AND status='waiting' AND NOT EXISTS ("
                    "SELECT 1 FROM agent_runs active WHERE active.session_id=agent_runs.session_id "
                    "AND active.id<>agent_runs.id AND active.status IN ('running','resume_queued'))",
                    (job_id, owner, (_now() + timedelta(seconds=lease_seconds)).isoformat(), run_id),
                ).rowcount
            if changed:
                claimed.append((run_id, user_id, session_id, job_id))
        return claimed

    def record_resume_failure(self, user_id: str, run_id: str, retry_seconds: float) -> bool:
        """Back off a failed resume and mark it terminal after three attempts.

        Args:
            user_id: Run owner.
            run_id: Run that failed to resume.
            retry_seconds: Base exponential backoff interval.

        Returns:
            Whether this failure exhausted the resume attempts.

        Raises:
            ValueError: The Run is absent or belongs to another user.
        """
        from datetime import timedelta

        row = self.db.execute(
            "SELECT resume_attempts FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
        if row is None:
            raise ValueError("Run owner mismatch")
        attempts = row[0] + 1
        terminal = attempts >= 3
        next_retry = (_now() + timedelta(seconds=retry_seconds * 2 ** (attempts - 1))).isoformat()
        with self.db:
            self.db.execute(
                "UPDATE agent_runs SET status=?,resume_attempts=?,retry_after=?,"
                "active_resume_job_id=NULL,"
                "lease_owner=NULL,lease_expires_at=NULL WHERE id=? AND user_id=?",
                ("failed" if terminal else "waiting", attempts, None if terminal else next_retry, run_id, user_id),
            )
        return terminal

    def recover_wakeups(self, retry_seconds: float = 1.0) -> None:
        """Recover active Run leases that have expired or lack an expiry.

        Args:
            retry_seconds: Base delay for a safe retry.
        """
        expired = self.db.execute(
            "SELECT id FROM agent_runs WHERE status IN ('running','resume_queued') "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
            (_now().isoformat(),),
        ).fetchall()
        for (run_id,) in expired:
            self._recover_interrupted(run_id, expired_only=True, retry_seconds=retry_seconds)

    def release_owned_runs(self, owner: str, retry_seconds: float = 1.0) -> None:
        """Recover active Runs leased by a worker that is stopping.

        Args:
            owner: Worker identity whose leases are released.
            retry_seconds: Base delay for a safe retry.
        """
        active = self.db.execute(
            "SELECT id FROM agent_runs WHERE lease_owner=? "
            "AND status IN ('running','resume_queued')", (owner,),
        ).fetchall()
        for (run_id,) in active:
            self._recover_interrupted(run_id, owner=owner, retry_seconds=retry_seconds)

    def recover_transient_initial_failure(
        self, run_id: str, owner: str, retry_seconds: float,
    ) -> None:
        """Retry a transient initial Pi failure only when no tool has started.

        Args:
            run_id: Affected initial Run.
            owner: Worker that holds its lease.
            retry_seconds: Base retry delay.
        """
        self._recover_interrupted(
            run_id, owner=owner, retry_seconds=retry_seconds,
            terminal_code="PI_RUN_FAILED", terminal_message="Pi Agent 执行失败",
        )

    def _recover_interrupted(
        self, run_id: str, *, expired_only: bool = False, owner: str | None = None,
        retry_seconds: float = 1.0, terminal_code: str = "PI_RUN_INTERRUPTED",
        terminal_message: str = "Pi Agent 执行被中断，请重新提交任务",
    ) -> None:
        """Resolve an interrupted Run under one SQLite write transaction.

        A Run with a persisted job or approval returns to waiting. A turn with
        no started tool may retry up to three attempts. Other interruptions
        fail closed and emit a terminal event.

        Args:
            run_id: Run to examine.
            expired_only: Leave a live lease untouched when true.
            owner: If set, require this worker to own the lease.
            retry_seconds: Base exponential retry delay.
            terminal_code: Error code for a terminal initial Run.
            terminal_message: User-visible terminal error text.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT user_id,session_id,status,lease_owner,lease_expires_at,"
                "initial_attempts,checkpoint_file,resume_attempts,active_resume_job_id,"
                "turn_start_seq "
                "FROM agent_runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                return
            (user_id, session_id, status, leased_by, expires_at, attempts, checkpoint_file,
             resume_attempts, active_resume_job_id, turn_start_seq) = row
            if status not in {"running", "resume_queued"}:
                return
            if owner is not None and leased_by != owner:
                return
            if expired_only and expires_at is not None and expires_at > _now().isoformat():
                return
            try:
                _reservation_id, period, _reserved, observed, charged = self._latest_token_reservation(
                    user_id, run_id,
                )
            except ValueError:
                pass  # Legacy runs may have no Token reservation.
            else:
                if observed:
                    self._adjust_token_reservation(user_id, run_id, period, observed - charged)
            has_job = self.db.execute(
                "SELECT 1 FROM agent_jobs WHERE run_id=?", (run_id,),
            ).fetchone() is not None
            has_approval = self.db.execute(
                "SELECT 1 FROM agent_approvals WHERE run_id=? AND status='pending'", (run_id,),
            ).fetchone() is not None
            can_wait = (status == "resume_queued" and checkpoint_file is not None) or (
                (has_job or has_approval) and checkpoint_file is not None
            )
            tool_started = self.db.execute(
                "SELECT 1 FROM agent_events WHERE run_id=? "
                "AND seq>? AND json_extract(payload, '$.type')='tool.started' LIMIT 1",
                (run_id, turn_start_seq),
            ).fetchone() is not None
            committed_session = self.db.execute(
                "SELECT 1 FROM pi_sessions WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone() is not None
            can_retry_resume = (
                status == "running" and active_resume_job_id is not None
                and committed_session and not tool_started and resume_attempts < 2
            )
            if can_retry_resume:
                delay = min(60.0, retry_seconds * (2 ** resume_attempts))
                self.db.execute(
                    "UPDATE agent_runs SET status='waiting',resume_attempts=resume_attempts+1,"
                    "retry_after=?,active_resume_job_id=NULL,lease_owner=NULL,"
                    "lease_expires_at=NULL WHERE id=?",
                    ((_now() + timedelta(seconds=delay)).isoformat(), run_id),
                )
                self._append_event_in_transaction(run_id, RunRetryingEvent(
                    run_id=run_id, data=RunRetryingData(
                        attempt=resume_attempts + 1, max_attempts=3,
                        delay_ms=round(delay * 1000), reset_message=True,
                    ),
                ))
                return
            can_retry_initial = (not can_wait and not has_job and not has_approval
                                 and active_resume_job_id is None
                                 and not tool_started and attempts < 2)
            if can_retry_initial:
                delay = min(60.0, retry_seconds * (2 ** attempts))
                self.db.execute(
                    "UPDATE agent_runs SET status='queued',initial_attempts=initial_attempts+1,"
                    "retry_after=?,lease_owner=NULL,lease_expires_at=NULL WHERE id=?",
                    ((_now() + timedelta(seconds=delay)).isoformat(), run_id),
                )
                self._append_event_in_transaction(run_id, RunRetryingEvent(
                    run_id=run_id, data=RunRetryingData(
                        attempt=attempts + 1, max_attempts=3,
                        delay_ms=round(delay * 1000), reset_message=True,
                    ),
                ))
                return
            self.db.execute(
                "UPDATE agent_runs SET status=?,active_resume_job_id=NULL,"
                "lease_owner=NULL,lease_expires_at=NULL WHERE id=?",
                ("waiting" if can_wait else "failed", run_id),
            )
            if not can_wait:
                resume_exhausted = (
                    active_resume_job_id is not None and not tool_started
                    and resume_attempts >= 2
                )
                self._append_event_in_transaction(run_id, RunFailedEvent(
                    run_id=run_id,
                    data=RunFailedData(
                        code="PI_RESUME_FAILED" if resume_exhausted else terminal_code,
                        message=("后台任务完成，但 Pi Agent 恢复失败"
                                 if resume_exhausted else terminal_message),
                    ),
                ))
