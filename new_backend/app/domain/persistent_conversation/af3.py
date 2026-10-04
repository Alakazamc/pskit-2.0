"""Af3 persistence methods for the conversation store."""

import hashlib
import json
import secrets
import uuid
from copy import copy
from datetime import datetime, timedelta

from app.contracts.capabilities import (
    Af3ComputeClaim,
    Af3FoldInput,
    Af3GpuReconciliation,
    Af3Job,
    Af3JobRequest,
    ComputeResourceRequirements,
    ComputeWorkerResources,
)
from app.contracts.catalog import ArtifactRef
from app.contracts.conversation import (
    ApprovalDecisionResponse,
    ApprovalRef,
    ApprovalRequiredData,
    ApprovalRequiredEvent,
    ApprovalResolvedData,
    ApprovalResolvedEvent,
    ArtifactCreatedData,
    ArtifactCreatedEvent,
    RunCancelledData,
    RunCancelledEvent,
    RunFailedData,
    RunFailedEvent,
    TaskUpdatedData,
    TaskUpdatedEvent,
    UsageUpdatedData,
    UsageUpdatedEvent,
)
from app.domain.af3_requests import Af3IdempotencyConflict, af3_request_fingerprint
from app.domain.quota import GpuQuotaExceeded

from .common import ComputeLeaseConflict, GpuReconciliationConflict
from .common import current_time as _now


class Af3Mixin:
    """Manage AF3 approvals, compute leases, GPU accounting, and artifacts."""

    def has_job_for_run(self, run_id: str) -> bool:
        """Return whether a Run has a queued or running AF3 job."""
        return self.db.execute(
            "SELECT 1 FROM agent_jobs WHERE run_id=? AND status IN ('queued','running','cancelling')",
            (run_id,),
        ).fetchone() is not None

    def has_any_job_for_run(self, run_id: str) -> bool:
        """Return whether any AF3 job was ever attached to a Run."""
        return self.db.execute(
            "SELECT 1 FROM agent_jobs WHERE run_id=?", (run_id,),
        ).fetchone() is not None

    def has_new_job_for_run(self, run_id: str, resumed_job_id: str) -> bool:
        """Return whether a Run created a job after its resumed job."""
        return self.db.execute(
            "SELECT 1 FROM agent_jobs WHERE run_id=? AND rowid>("
            "SELECT rowid FROM agent_jobs WHERE id=? AND run_id=?) LIMIT 1",
            (run_id, resumed_job_id, run_id),
        ).fetchone() is not None

    def has_started_tool_this_turn(self, run_id: str) -> bool:
        """Return whether a tool started after this Run's current turn boundary."""
        return self.db.execute(
            "SELECT 1 FROM agent_events e JOIN agent_runs r ON r.id=e.run_id "
            "WHERE e.run_id=? AND e.seq>r.turn_start_seq "
            "AND json_extract(e.payload, '$.type')='tool.started' LIMIT 1",
            (run_id,),
        ).fetchone() is not None

    def has_pending_approval_for_run(self, run_id: str) -> bool:
        """Return whether an AF3 approval still blocks the Run."""
        return self.db.execute(
            "SELECT 1 FROM agent_approvals WHERE run_id=? AND status='pending'", (run_id,),
        ).fetchone() is not None

    def request_af3_approval(
        self, user_id: str, run_id: str, tool_call_id: str, estimated_minutes: int,
        fold_input: Af3FoldInput | None = None,
    ) -> ApprovalRef | Af3Job:
        """Create or reuse an AF3 approval for a Run tool call.

        Reusing the same tool call with different inputs is rejected. An
        already approved call returns its job instead of requesting approval.

        Args:
            user_id: Run owner.
            run_id: Run requesting AF3 execution.
            tool_call_id: Stable Pi tool call identifier.
            estimated_minutes: GPU minutes shown for approval.
            fold_input: Optional AF3 input saved with the request.

        Returns:
            Pending approval reference or the previously approved job.

        Raises:
            ValueError: Ownership, inputs, or prior decision conflict.
        """
        if not self.owns_run(user_id, run_id):
            raise ValueError("Run owner mismatch")
        with self._immediate_transaction():
            supplied = fold_input.model_dump_json(by_alias=True, exclude_none=True) if fold_input else None
            existing = self.db.execute(
                "SELECT id,status,job_id,estimated_minutes,input_json FROM agent_approvals "
                "WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id),
            ).fetchone()
            if existing:
                approval_id, status, job_id, estimate, input_json = existing
                if input_json != supplied or estimate != estimated_minutes:
                    raise ValueError("Approval arguments conflict with prior submission")
                if status == "approved" and job_id:
                    return self.get_af3_job(user_id, job_id)
                if status == "rejected":
                    raise ValueError("Approval already rejected")
                return ApprovalRef(approval_id=approval_id, estimated_gpu_minutes=estimate)
            approval_id = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO agent_approvals "
                "(id,user_id,run_id,tool_call_id,estimated_minutes,status,job_id,created_at,input_json) "
                "VALUES (?,?,?,?,?,'pending',NULL,?,?)",
                (approval_id, user_id, run_id, tool_call_id, estimated_minutes,
                 _now().isoformat(), supplied),
            )
            self._append_event_in_transaction(run_id, ApprovalRequiredEvent(
                run_id=run_id, data=ApprovalRequiredData(
                    approval_id=approval_id, capability="submit_af3",
                    estimated_gpu_minutes=estimated_minutes,
                ),
            ))
        return ApprovalRef(approval_id=approval_id, estimated_gpu_minutes=estimated_minutes)

    def decide_af3_approval(
        self, user_id: str, run_id: str, approval_id: str, decision: str,
    ) -> ApprovalDecisionResponse | None:
        """Apply an AF3 approval decision exactly once under a write lock.

        Approval queues a job after checking the user's GPU quota. Rejection
        cancels the Run; repeated identical decisions return the saved result.

        Args:
            user_id: Approval and Run owner.
            run_id: Run awaiting the decision.
            approval_id: Approval record to resolve.
            decision: ``approved`` or ``rejected``.

        Returns:
            Decision and optional job ID, or ``None`` if no owned record exists.

        Raises:
            ValueError: The decision conflicts or the Run is no longer active.
            GpuQuotaExceeded: Approval would exceed the daily GPU limit.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT tool_call_id,estimated_minutes,status,job_id,input_json FROM agent_approvals "
                "WHERE id=? AND user_id=? AND run_id=?", (approval_id, user_id, run_id),
            ).fetchone()
            if row is None:
                return None
            tool_call_id, estimated_minutes, previous, job_id, input_json = row
            if previous != "pending":
                if previous != decision:
                    raise ValueError("Approval decision conflicts with prior decision")
                return ApprovalDecisionResponse(approval_id=approval_id, status=previous, job_id=job_id)
            run_status = self.db.execute(
                "SELECT status FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id),
            ).fetchone()[0]
            if run_status not in {"running", "waiting"}:
                raise ValueError("Run is no longer active")
            if decision == "approved":
                used, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
                if estimated_minutes > max(0, self._gpu_limit_for(user_id) - used - reserved):
                    raise GpuQuotaExceeded
                job_id = str(uuid.uuid4())
                self.db.execute(
                    "INSERT INTO agent_jobs "
                    "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,"
                    "created_at,input_json,resource_requirements_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job_id, user_id, run_id, tool_call_id, "queued", 0,
                     estimated_minutes, _now().isoformat(), input_json,
                     self.af3_resources.model_dump_json()),
                )
                self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                    run_id=run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3", status="queued", progress=0),
                ))
            else:
                self.db.execute(
                    "UPDATE agent_runs SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL "
                    "WHERE id=?", (run_id,),
                )
            self.db.execute(
                "UPDATE agent_approvals SET status=?,job_id=? WHERE id=?",
                (decision, job_id, approval_id),
            )
            self._append_event_in_transaction(run_id, ApprovalResolvedEvent(
                run_id=run_id,
                data=ApprovalResolvedData(
                    approval_id=approval_id, decision=decision, job_id=job_id,
                ),
            ))
            if decision == "rejected":
                self._append_event_in_transaction(run_id, RunCancelledEvent(
                    run_id=run_id, data=RunCancelledData(),
                ))
        return ApprovalDecisionResponse(approval_id=approval_id, status=decision, job_id=job_id)

    def create_af3_job(
        self, user_id: str, estimated_minutes: int, run_id: str | None = None,
        tool_call_id: str | None = None,
        fold_input: Af3FoldInput | None = None,
        *, simulation: bool = False, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Create or reuse a quota-reserved AF3 job atomically.

        A public idempotency key and a Run tool call each prevent duplicate
        submissions with the same inputs. New jobs start in ``queued`` state.

        Args:
            user_id: Job owner.
            estimated_minutes: GPU minutes reserved immediately.
            run_id: Optional Run to attach and notify.
            tool_call_id: Optional Pi tool call identifier.
            fold_input: Optional AF3 model input.
            simulation: Whether this is a mock job.
            idempotency_key: Optional public request key.

        Returns:
            Created or previously matching job.

        Raises:
            ValueError: Run ownership or repeated tool call arguments conflict.
            Af3IdempotencyConflict: A public key was reused with different input.
            GpuQuotaExceeded: The daily GPU allowance is insufficient.
        """
        if run_id and not self.owns_run(user_id, run_id):
            raise ValueError("Run owner mismatch")
        job_id = str(uuid.uuid4())
        request_hash = af3_request_fingerprint(Af3JobRequest(
            estimated_gpu_minutes=estimated_minutes, run_id=run_id, fold_input=fold_input,
        )) if idempotency_key else None
        with self._immediate_transaction():
            supplied = fold_input.model_dump_json(by_alias=True, exclude_none=True) if fold_input else None
            if idempotency_key:
                previous = self.db.execute(
                    "SELECT request_hash,job_id FROM agent_af3_request_keys "
                    "WHERE user_id=? AND key=?", (user_id, idempotency_key),
                ).fetchone()
                if previous:
                    if previous[0] != request_hash:
                        raise Af3IdempotencyConflict
                    return self.get_af3_job(user_id, previous[1])
            if run_id and tool_call_id:
                existing = self.db.execute(
                    "SELECT id,estimated_minutes,input_json FROM agent_jobs "
                    "WHERE run_id=? AND tool_call_id=?",
                    (run_id, tool_call_id),
                ).fetchone()
                if existing:
                    if existing[1] != estimated_minutes or existing[2] != supplied:
                        raise ValueError("AF3 tool call arguments conflict with prior submission")
                    return self.get_af3_job(user_id, existing[0])
            used, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
            if estimated_minutes > max(0, self._gpu_limit_for(user_id) - used - reserved):
                raise GpuQuotaExceeded
            self.db.execute(
                "INSERT INTO agent_jobs "
                "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,created_at,"
                "input_json,simulation,resource_requirements_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, user_id, run_id, tool_call_id, "queued", 0,
                 estimated_minutes, _now().isoformat(), supplied, int(simulation),
                 self.af3_resources.model_dump_json()),
            )
            if idempotency_key:
                self.db.execute(
                    "INSERT INTO agent_af3_request_keys "
                    "(user_id,key,request_hash,job_id,created_at) VALUES (?,?,?,?,?)",
                    (user_id, idempotency_key, request_hash, job_id, _now().isoformat()),
                )
        if run_id:
            self.append_event(
                user_id, run_id,
                TaskUpdatedEvent(
                    run_id=run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3", status="queued", progress=0),
                ),
            )
        return self.get_af3_job(user_id, job_id)

    def get_af3_job(self, user_id: str, job_id: str) -> Af3Job | None:
        """Read an AF3 job only when it belongs to the user.

        Args:
            user_id: Job owner.
            job_id: Job to retrieve.

        Returns:
            Job snapshot, or ``None`` when it is unavailable.
        """
        import json

        row = self.db.execute(
            "SELECT id,status,progress,estimated_minutes,actual_minutes,run_id,artifacts,"
            "input_json,simulation,resource_requirements_json,gpu_accounting_status "
            "FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=? AND user_id=?", (job_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return Af3Job(
            id=row[0], status=row[1], progress=row[2], estimated_gpu_minutes=row[3],
            actual_gpu_minutes=row[4], run_id=row[5], artifacts=json.loads(row[6]),
            fold_input=json.loads(row[7]) if row[7] else None,
            simulation=bool(row[8]),
            resource_requirements=ComputeResourceRequirements.model_validate_json(row[9]),
            gpu_accounting_status=row[10],
        )

    def af3_job_for_compute(self, job_id: str) -> Af3Job | None:
        """Read a job by ID for authenticated internal compute code.

        Args:
            job_id: Job to retrieve.

        Returns:
            Job snapshot, or ``None`` when absent.
        """
        row = self.db.execute("SELECT user_id FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=?", (job_id,)).fetchone()
        return self.get_af3_job(row[0], job_id) if row else None

    def claim_compute_jobs(
        self, worker_id: str, resources: ComputeWorkerResources, *,
        lease_seconds: int = 60, limit: int = 1,
        max_execution_seconds: int = 21600,
        require_explicit_memory: bool = False,
    ) -> list[Af3ComputeClaim]:
        """Atomically lease compatible queued AF3 compute jobs.

        A missing heartbeat is not proof that the GPU process stopped. A
        running job remains owned by its original worker until settlement or
        the absolute execution deadline, preventing duplicate GPU runs.

        Args:
            worker_id: Compute worker identity.
            resources: Available capabilities and GPU capacity.
            lease_seconds: Duration of each compute lease.
            limit: Maximum jobs to claim.
            max_execution_seconds: Absolute runtime limit since first claim.
            require_explicit_memory: Reject jobs without a memory requirement.

        Returns:
            Compatible jobs with lease tokens for this worker.
        """
        now = _now()
        claimed: list[Af3ComputeClaim] = []
        postgres = getattr(self, "database", None) is not None
        transaction = self.db if postgres else self._immediate_transaction()
        with transaction:
            if postgres:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (f"pskit-af3-worker:{worker_id}",),
                )
            active_requirements = self.db.execute(
                "SELECT resource_requirements_json FROM agent_jobs "
                "WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND worker_id=? AND status='running'",
                (worker_id,),
            ).fetchall()
            available_gpus = resources.gpu_count - sum(
                ComputeResourceRequirements.model_validate_json(row[0]).gpu_count
                for row in active_requirements
            )
            rows = self.db.execute(
                "SELECT id,user_id,run_id,attempts,resource_requirements_json FROM agent_jobs "
                "WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND input_json IS NOT NULL AND status='queued' "
                "ORDER BY created_at,rowid",
            ).fetchall()
            for job_id, user_id, run_id, attempts, requirements_json in rows:
                required = ComputeResourceRequirements.model_validate_json(requirements_json)
                if ((require_explicit_memory and required.min_gpu_memory_mb == 0)
                        or required.capability not in resources.capabilities
                        or required.gpu_count > available_gpus
                        or required.min_gpu_memory_mb > resources.gpu_memory_mb):
                    continue
                if postgres and self.db.execute(
                    "SELECT id FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=? AND status='queued' "
                    "FOR UPDATE SKIP LOCKED",
                    (job_id,),
                ).fetchone() is None:
                    continue
                lease_token = secrets.token_urlsafe(32)
                changed = self.db.execute(
                    "UPDATE agent_jobs SET status='running',worker_id=?,lease_expires_at=?,lease_token=?, "
                    "attempts=attempts+1,first_claimed_at=COALESCE(first_claimed_at,?) "
                    "WHERE id=? AND status='queued'",
                    (worker_id, (now + timedelta(seconds=lease_seconds)).isoformat(),
                     lease_token, now.isoformat(), job_id),
                ).rowcount
                if not changed:
                    continue
                if run_id:
                    self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                        run_id=run_id,
                        data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                             status="running", progress=0),
                    ))
                job = self.get_af3_job(user_id, job_id)
                if job is not None:
                    claimed.append(Af3ComputeClaim(**job.model_dump(), attempt=attempts + 1,
                                                   lease_token=lease_token))
                    available_gpus -= required.gpu_count
                if len(claimed) >= limit or available_gpus < 1:
                    break
        return claimed

    def owned_compute_jobs(self, worker_id: str) -> list[Af3ComputeClaim]:
        """Recover active claims after a worker lost its HTTP response or journal write."""
        rows = self.db.execute(
            "SELECT id,user_id,attempts,lease_token FROM agent_jobs "
            "WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND worker_id=? AND status='running' ORDER BY created_at,rowid",
            (worker_id,),
        ).fetchall()
        claims = []
        for job_id, user_id, attempt, token in rows:
            job = self.get_af3_job(user_id, job_id)
            if job is not None and token:
                claims.append(Af3ComputeClaim(**job.model_dump(), attempt=attempt,
                                               lease_token=token))
        return claims

    def expire_queued_compute_jobs(self, max_age_seconds: int) -> int:
        """Fail unclaimed jobs past their queue deadline and release GPU holds.

        Args:
            max_age_seconds: Maximum time since job creation.

        Returns:
            Number of jobs expired in this transaction.
        """
        now = _now()
        cutoff = (now - timedelta(seconds=max_age_seconds)).isoformat()
        expired = 0
        with self._immediate_transaction():
            rows = self.db.execute(
                "SELECT id,user_id,run_id FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND input_json IS NOT NULL "
                "AND status='queued' AND created_at<=?", (cutoff,),
            ).fetchall()
            for job_id, user_id, run_id in rows:
                self.db.execute(
                    "UPDATE agent_jobs SET status='failed',progress=0,actual_minutes=0,"
                    "gpu_accounting_status='released' "
                    "WHERE id=? AND status='queued'", (job_id,),
                )
                expired += 1
                if run_id is None:
                    continue
                self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                    run_id=run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                         status="failed", progress=0),
                ))
                used, reserved = self._gpu_usage_values(user_id, now.date().isoformat())
                self._append_event_in_transaction(run_id, UsageUpdatedEvent(
                    run_id=run_id,
                    data=UsageUpdatedData(
                        gpu_remaining=max(0, self._gpu_limit_for(user_id) - used - reserved),
                    ),
                ))
                changed = self.db.execute(
                    "UPDATE agent_runs SET status='failed',lease_owner=NULL,lease_expires_at=NULL "
                    "WHERE id=? AND status IN ('queued','running','waiting','resume_queued')",
                    (run_id,),
                ).rowcount
                if changed:
                    self._append_event_in_transaction(run_id, RunFailedEvent(
                        run_id=run_id,
                        data=RunFailedData(
                            code="AF3_COMPUTE_UNAVAILABLE",
                            message="AlphaFold 3 计算节点未在等待期限内领取任务",
                        ),
                    ))
        return expired

    def expire_running_compute_jobs(self, max_execution_seconds: int) -> int:
        """Fail overdue running jobs while holding their GPU charge for review.

        The charge enters ``pending_reconciliation`` because a worker may
        have consumed GPU time before its lease was lost.

        Args:
            max_execution_seconds: Maximum time since first compute claim.

        Returns:
            Number of jobs expired in this transaction.
        """
        now = _now()
        cutoff = (now - timedelta(seconds=max_execution_seconds)).isoformat()
        expired = 0
        with self._immediate_transaction():
            rows = self.db.execute(
                "SELECT id,user_id,run_id FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND input_json IS NOT NULL "
                "AND status='running' AND first_claimed_at<=?", (cutoff,),
            ).fetchall()
            for job_id, user_id, run_id in rows:
                self.db.execute(
                    "UPDATE agent_jobs SET status='failed',progress=0,actual_minutes=NULL,"
                    "gpu_accounting_status='pending_reconciliation',"
                    "worker_id=NULL,lease_expires_at=NULL "
                    "WHERE id=? AND status='running'", (job_id,),
                )
                self.db.execute("DELETE FROM agent_artifact_blobs WHERE job_id=?", (job_id,))
                expired += 1
                if run_id is None:
                    continue
                self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                    run_id=run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                         status="failed", progress=0),
                ))
                used, reserved = self._gpu_usage_values(user_id, now.date().isoformat())
                self._append_event_in_transaction(run_id, UsageUpdatedEvent(
                    run_id=run_id,
                    data=UsageUpdatedData(
                        gpu_remaining=max(0, self._gpu_limit_for(user_id) - used - reserved),
                    ),
                ))
                changed = self.db.execute(
                    "UPDATE agent_runs SET status='failed',lease_owner=NULL,lease_expires_at=NULL "
                    "WHERE id=? AND status IN ('queued','running','waiting','resume_queued')",
                    (run_id,),
                ).rowcount
                if changed:
                    self._append_event_in_transaction(run_id, RunFailedEvent(
                        run_id=run_id,
                        data=RunFailedData(
                            code="AF3_EXECUTION_TIMEOUT",
                            message="AlphaFold 3 任务超过最长执行时间",
                        ),
                    ))
        return expired

    def renew_compute_lease(
        self, job_id: str, worker_id: str, lease_token: str, *, lease_seconds: int = 60,
        max_execution_seconds: int = 21600,
    ) -> bool:
        """Extend a compute lease for its original worker, including after an outage.

        Args:
            job_id: Running AF3 job.
            worker_id: Worker holding the lease.
            lease_token: Current fencing token.
            lease_seconds: New lease duration.
            max_execution_seconds: Absolute execution deadline.

        Returns:
            Whether exactly one live lease was renewed.
        """
        now = _now()
        with self._immediate_transaction():
            changed = self.db.execute(
                "UPDATE agent_jobs SET lease_expires_at=? "
                "WHERE id=? AND worker_id=? AND lease_token=? "
                "AND status='running' AND first_claimed_at>?",
                ((now + timedelta(seconds=lease_seconds)).isoformat(),
                 job_id, worker_id, lease_token,
                 (now - timedelta(seconds=max_execution_seconds)).isoformat()),
            ).rowcount
        return changed == 1

    def update_compute_progress(
        self, job_id: str, worker_id: str, lease_token: str, attempt: int,
        progress: int, *, max_execution_seconds: int = 21600,
    ) -> Af3Job | None:
        """Durably record monotonic progress from the owning compute attempt."""
        now = _now()
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT user_id,run_id,status,worker_id,lease_token,attempts,"
                "first_claimed_at,progress FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=?", (job_id,),
            ).fetchone()
            if row is None:
                return None
            (user_id, run_id, status, owner, token, current_attempt,
             first_claimed_at, previous_progress) = row
            if (status != "running" or owner != worker_id or token != lease_token
                    or current_attempt != attempt or not first_claimed_at
                    or first_claimed_at <= (
                        now - timedelta(seconds=max_execution_seconds)
                    ).isoformat()):
                raise ComputeLeaseConflict
            if progress > previous_progress:
                self.db.execute("UPDATE agent_jobs SET progress=? WHERE id=?", (progress, job_id))
                if run_id:
                    self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                        run_id=run_id,
                        data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                             status="running", progress=progress),
                    ))
            return self.get_af3_job(user_id, job_id)

    def cancel_af3_job(self, user_id: str, job_id: str, *, connection=None) -> Af3Job | None:
        """Cancel an owned active AF3 job and update GPU accounting.

        Never-started jobs release their hold; started jobs await actual GPU
        usage reconciliation. A terminal job is returned unchanged.

        Args:
            user_id: Job owner.
            job_id: Job to cancel.

        Returns:
            Latest job snapshot, or ``None`` when unavailable.
        """
        if connection is None:
            with self._immediate_transaction():
                return self._cancel_af3_job(user_id, job_id)
        # Bind a local repository view to the caller's transaction, including events.
        bound = copy(self)
        bound.db = connection
        return bound._cancel_af3_job(user_id, job_id)

    def _cancel_af3_job(self, user_id: str, job_id: str) -> Af3Job | None:
        """Apply cancellation on the current caller-owned transaction connection."""
        job = self.get_af3_job(user_id, job_id)
        if job is None:
            return None
        if job.status in {"queued", "running"}:
            changed = self.db.execute(
                "UPDATE agent_jobs SET status='cancelled',worker_id=NULL,"
                "lease_expires_at=NULL,"
                "gpu_accounting_status=CASE WHEN attempts>0 "
                "THEN 'pending_reconciliation' ELSE 'released' END,"
                "actual_minutes=CASE WHEN attempts>0 THEN NULL ELSE 0 END "
                "WHERE id=? AND user_id=? "
                "AND status IN ('queued','running')", (job_id, user_id),
            ).rowcount
            if changed and job.run_id:
                self._append_event_in_transaction(job.run_id, TaskUpdatedEvent(
                    run_id=job.run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                         status="cancelled", progress=job.progress),
                ))
                used, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
                self._append_event_in_transaction(job.run_id, UsageUpdatedEvent(
                    run_id=job.run_id,
                    data=UsageUpdatedData(
                        gpu_remaining=max(0, self._gpu_limit_for(user_id)-used-reserved),
                    ),
                ))
        return self.get_af3_job(user_id, job_id)

    def settle_af3_job(
        self, job_id: str, status: str, actual_minutes: int,
        artifacts: list[dict[str, str]], attempt: int | None = None,
        lease_token: str | None = None,
        simulation: bool = False,
        max_execution_seconds: int = 21600,
    ) -> Af3Job | None:
        """Settle an AF3 result with lease fencing and durable usage events.

        A late result for a job awaiting reconciliation can report actual GPU
        minutes without reopening the job. Repeating the same settled report
        returns the saved job; conflicting attempts are rejected.

        Args:
            job_id: AF3 job being settled.
            status: Terminal result state.
            actual_minutes: GPU minutes reported by the worker.
            artifacts: Completed artifact metadata.
            attempt: Claimed worker attempt number.
            lease_token: Fencing token issued for that attempt.
            simulation: Whether a mock worker produced the result.
            max_execution_seconds: Absolute deadline for a live claim.

        Returns:
            Latest job snapshot, or ``None`` when absent.

        Raises:
            ComputeLeaseConflict: The worker attempt or lease is stale.
            GpuReconciliationConflict: A late GPU report conflicts with a
                previously reconciled amount.
            ValueError: Artifact metadata conflicts with uploaded content.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT user_id,run_id,status,worker_id,lease_expires_at,attempts,lease_token,"
                "first_claimed_at,gpu_accounting_status,actual_minutes "
                "FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            (user_id, run_id, previous, worker_id, _lease_expires_at, current_attempt,
             current_token, first_claimed_at, accounting_status, prior_minutes) = row
            if previous not in {"queued", "running"}:
                if accounting_status in {"pending_reconciliation", "reconciled"}:
                    if (current_attempt < 1 or attempt != current_attempt
                            or not current_token or lease_token != current_token):
                        raise ComputeLeaseConflict
                    if accounting_status == "reconciled":
                        if actual_minutes != prior_minutes:
                            raise GpuReconciliationConflict(
                                "GPU usage conflicts with prior reconciliation"
                            )
                        return self.get_af3_job(user_id, job_id)
                    self.db.execute(
                        "UPDATE agent_jobs SET actual_minutes=?,"
                        "gpu_accounting_status='reconciled' WHERE id=? "
                        "AND gpu_accounting_status='pending_reconciliation'",
                        (actual_minutes, job_id),
                    )
                    self._record_gpu_reconciliation(
                        job_id, user_id, "worker", prior_minutes, actual_minutes,
                        "late_worker_report",
                    )
                    if run_id:
                        used, reserved = self._gpu_usage_values(
                            user_id, _now().date().isoformat(),
                        )
                        self._append_event_in_transaction(run_id, UsageUpdatedEvent(
                            run_id=run_id,
                            data=UsageUpdatedData(gpu_remaining=max(
                                0, self._gpu_limit_for(user_id)-used-reserved,
                            )),
                        ))
                return self.get_af3_job(user_id, job_id)
            if worker_id is not None and (
                attempt != current_attempt or lease_token != current_token
                or not first_claimed_at
                or first_claimed_at <= (
                    _now() - timedelta(seconds=max_execution_seconds)
                ).isoformat()
            ):
                raise ComputeLeaseConflict
            for artifact in artifacts:
                blob = self.db.execute(
                    "SELECT job_id,name,kind FROM agent_artifact_blobs WHERE id=?",
                    (artifact["id"],),
                ).fetchone()
                if blob is not None and blob != (
                    job_id, artifact["name"], artifact["kind"],
                ):
                    raise ValueError("Artifact metadata conflicts with uploaded content")
            self.db.execute(
                "UPDATE agent_jobs SET status=?,progress=?,actual_minutes=?,artifacts=?, "
                "worker_id=NULL,lease_expires_at=NULL,lease_token=NULL,simulation=?,"
                "gpu_accounting_status='settled' "
                "WHERE id=? AND status IN ('queued','running')",
                (status, 100 if status == "completed" else 0, actual_minutes,
                 json.dumps(artifacts if status == "completed" else []), int(simulation), job_id),
            )
            if run_id:
                self._append_event_in_transaction(run_id, TaskUpdatedEvent(
                    run_id=run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                         status=status, progress=100 if status == "completed" else 0),
                ))
                used, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
                self._append_event_in_transaction(run_id, UsageUpdatedEvent(
                    run_id=run_id,
                    data=UsageUpdatedData(gpu_remaining=max(0, self._gpu_limit_for(user_id)-used-reserved)),
                ))
                if status == "completed":
                    for artifact in artifacts:
                        self._append_event_in_transaction(run_id, ArtifactCreatedEvent(
                            run_id=run_id,
                            data=ArtifactCreatedData(artifact_id=artifact["id"],
                                                     name=artifact["name"], kind=artifact["kind"]),
                        ))
                else:
                    changed = self.db.execute(
                        "UPDATE agent_runs SET status='failed',lease_owner=NULL,lease_expires_at=NULL "
                        "WHERE id=? "
                        "AND status IN ('queued','running','waiting','resume_queued')",
                        (run_id,),
                    ).rowcount
                    if changed:
                        self._append_event_in_transaction(run_id, RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(code="AF3_TASK_FAILED", message="AlphaFold 3 任务失败"),
                        ))
        return self.get_af3_job(user_id, job_id)

    def _record_gpu_reconciliation(
        self, job_id: str, user_id: str, source: str,
        previous_minutes: int | None, actual_minutes: int, reason: str,
    ) -> None:
        """Append a GPU reconciliation audit row in the caller's transaction."""
        self.db.execute(
            "INSERT INTO agent_gpu_reconciliations "
            "(job_id,user_id,source,previous_minutes,actual_minutes,reason,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, user_id, source, previous_minutes, actual_minutes,
             reason, _now().isoformat()),
        )

    def reconcile_af3_gpu_usage(
        self, job_id: str, actual_minutes: int, reason: str, *,
        audit_callback=None, audit_actor: str = 'operator:legacy-admin-key', audit_request_id=None,
        connection=None,
    ) -> Af3Job | None:
        """Set actual GPU minutes for a terminal AF3 job with an audit reason.

        Args:
            job_id: Job whose accounting is corrected.
            actual_minutes: Nonnegative GPU minutes to charge.
            reason: Nonempty operator explanation.

        Returns:
            Updated job, or ``None`` when the job is absent.

        Raises:
            ValueError: Inputs are invalid or the job is still active.
        """
        options = {
            "audit_callback": audit_callback, "audit_actor": audit_actor,
            "audit_request_id": audit_request_id,
        }
        if connection is None:
            with self._immediate_transaction():
                return self._reconcile_af3_gpu_usage(job_id, actual_minutes, reason, **options)
        bound = copy(self)
        bound.db = connection
        return bound._reconcile_af3_gpu_usage(job_id, actual_minutes, reason, **options)

    def _reconcile_af3_gpu_usage(
        self, job_id, actual_minutes, reason, *, audit_callback, audit_actor, audit_request_id,
    ):
        """Apply native-minute settlement and events on the current transaction."""
        if actual_minutes < 0 or not reason.strip():
            raise ValueError("GPU reconciliation requires nonnegative minutes and a reason")
        row = self.db.execute(
            "SELECT user_id,run_id,status,actual_minutes,gpu_accounting_status "
            "FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' AND id=?", (job_id,),
        ).fetchone()
        if row is None:
            return None
        user_id, run_id, status, prior_minutes, accounting_status = row
        if status in {"queued", "running"}:
            raise ValueError("AF3 job is still active")
        if accounting_status in {"settled", "reconciled"} and prior_minutes == actual_minutes:
            return self.get_af3_job(user_id, job_id)
        self.db.execute(
            "UPDATE agent_jobs SET actual_minutes=?,gpu_accounting_status='reconciled' "
            "WHERE id=?", (actual_minutes, job_id),
        )
        self._record_gpu_reconciliation(
            job_id, user_id, "admin", prior_minutes, actual_minutes, reason.strip(),
        )
        if audit_callback:
            audit_callback(audit_actor, 'usage:reconcile-af3', job_id, reason.strip(),
                           {'actual_gpu_minutes': prior_minutes, 'accounting_status': accounting_status},
                           {'actual_gpu_minutes': actual_minutes, 'accounting_status': 'reconciled'},
                           audit_request_id, connection=self.db)
        if run_id:
            used, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
            self._append_event_in_transaction(run_id, UsageUpdatedEvent(
                run_id=run_id,
                data=UsageUpdatedData(gpu_remaining=max(
                    0, self._gpu_limit_for(user_id)-used-reserved,
                )),
            ))
        return self.get_af3_job(user_id, job_id)

    def gpu_reconciliations_for(self, job_id: str) -> list[Af3GpuReconciliation]:
        """Read the ordered GPU accounting audit trail for one job.

        Args:
            job_id: Job whose reconciliation history is requested.

        Returns:
            Audit records in insertion order.
        """
        rows = self.db.execute(
            "SELECT id,job_id,user_id,source,previous_minutes,actual_minutes,reason,created_at "
            "FROM agent_gpu_reconciliations WHERE job_id=? ORDER BY id", (job_id,),
        ).fetchall()
        return [Af3GpuReconciliation(
            id=row[0], job_id=row[1], user_id=row[2], source=row[3],
            previous_minutes=row[4], actual_minutes=row[5], reason=row[6],
            created_at=datetime.fromisoformat(row[7]),
        ) for row in rows]

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        """List queued and running AF3 job IDs attached to an owned Run.

        Args:
            user_id: Job owner.
            run_id: Run whose active jobs are requested.

        Returns:
            Active job IDs.
        """
        return [row[0] for row in self.db.execute(
            "SELECT id FROM agent_jobs WHERE user_id=? AND run_id=? "
            "AND status IN ('queued','running')", (user_id, run_id),
        ).fetchall()]

    def af3_artifacts_for(self, user_id: str) -> list[dict[str, str]]:
        """List completed AF3 artifact metadata with blob availability.

        Args:
            user_id: Owner whose completed jobs are inspected.

        Returns:
            Artifact metadata, size, digest, and availability fields.
        """
        import json

        rows = self.db.execute(
            "SELECT id,artifacts FROM agent_jobs WHERE user_id=? AND status='completed'", (user_id,)
        ).fetchall()
        result = []
        for job_id, raw_artifacts in rows:
            for artifact in json.loads(raw_artifacts):
                blob = self.db.execute(
                    "SELECT size,sha256 FROM agent_artifact_blobs "
                    "WHERE id=? AND user_id=? AND job_id=?",
                    (artifact["id"], user_id, job_id),
                ).fetchone()
                result.append({**artifact, "available": blob is not None,
                               "size": blob[0] if blob else None,
                               "sha256": blob[1] if blob else None})
        return result

    def save_artifact_blob(
        self, job_id: str, artifact_id: str, name: str, kind: str, content: bytes,
        attempt: int | None = None, lease_token: str | None = None,
        max_execution_seconds: int = 21600,
    ) -> ArtifactRef | None:
        """Store artifact bytes under a job with lease and digest checks.

        A repeated upload must match the saved content and metadata exactly.
        New uploads to terminal jobs are rejected.

        Args:
            job_id: Owning AF3 job.
            artifact_id: Stable artifact identifier.
            name: Artifact file name.
            kind: Artifact category.
            content: Bytes to persist.
            attempt: Worker attempt number for a leased job.
            lease_token: Current compute fencing token.
            max_execution_seconds: Absolute live claim deadline.

        Returns:
            Stored artifact reference, or ``None`` when the job is absent.

        Raises:
            ComputeLeaseConflict: The worker lease is stale.
            ValueError: Uploaded content conflicts or the job is closed.
        """
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("Artifact exceeds the 20 MiB limit")
        digest = hashlib.sha256(content).hexdigest()
        with self._immediate_transaction():
            job = self.db.execute(
                "SELECT user_id,status,worker_id,lease_expires_at,attempts,lease_token,"
                "first_claimed_at "
                "FROM agent_jobs WHERE id=?", (job_id,),
            ).fetchone()
            if job is None:
                return None
            (user_id, status, worker_id, _lease_expires_at, current_attempt,
             current_token, first_claimed_at) = job
            if status in {"queued", "running"} and worker_id is not None and (
                attempt != current_attempt or lease_token != current_token
                or not first_claimed_at
                or first_claimed_at <= (
                    _now() - timedelta(seconds=max_execution_seconds)
                ).isoformat()
            ):
                raise ComputeLeaseConflict
            existing = self.db.execute(
                "SELECT job_id,name,kind,size,sha256 FROM agent_artifact_blobs WHERE id=?",
                (artifact_id,),
            ).fetchone()
            if existing:
                if existing != (job_id, name, kind, len(content), digest):
                    raise ValueError("Artifact upload conflicts with existing content")
            else:
                if status not in {"queued", "running"}:
                    raise ValueError("Artifact upload closed")
                self.db.execute(
                    "INSERT INTO agent_artifact_blobs "
                    "(id,user_id,job_id,name,kind,size,sha256,content,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (artifact_id, user_id, job_id, name, kind, len(content), digest,
                     content, _now().isoformat()),
                )
        return ArtifactRef(id=artifact_id, name=name, kind=kind, available=True,
                           size=len(content), sha256=digest)

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        """Read bytes only for an owned artifact of a completed AF3 job.

        Args:
            user_id: Artifact and job owner.
            artifact_id: Artifact to download.

        Returns:
            File name and bytes, or ``None`` when unavailable.
        """
        row = self.db.execute(
            "SELECT blob.name,blob.content FROM agent_artifact_blobs blob "
            "JOIN agent_jobs job ON job.id=blob.job_id "
            "WHERE blob.id=? AND blob.user_id=? AND job.user_id=? AND job.status='completed'",
            (artifact_id, user_id, user_id),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def available_gpu(self, user_id: str) -> int:
        """Return unspent daily GPU minutes after settled and reserved jobs.

        Args:
            user_id: Account whose remaining allowance is requested.

        Returns:
            Nonnegative GPU minutes currently available.
        """
        actual, reserved = self._gpu_usage_values(user_id, _now().date().isoformat())
        return max(0, self._gpu_limit_for(user_id) - actual - reserved)
