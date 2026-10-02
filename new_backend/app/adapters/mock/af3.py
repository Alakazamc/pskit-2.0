import uuid
from time import monotonic

from app.contracts.capabilities import Af3Job, Af3JobRequest
from app.contracts.conversation import (
    ArtifactCreatedData,
    ArtifactCreatedEvent,
    MessageDeltaData,
    MessageDeltaEvent,
    RunCompletedData,
    RunCompletedEvent,
    TaskUpdatedData,
    TaskUpdatedEvent,
    UsageUpdatedData,
    UsageUpdatedEvent,
)
from app.domain.conversation import ConversationStore
from app.domain.af3_requests import Af3IdempotencyConflict, af3_request_fingerprint
from app.domain.quota import QuotaLedger


class MockAf3:
    def __init__(self, quotas: QuotaLedger, conversations: ConversationStore) -> None:
        """Keep simulated jobs and idempotency keys in memory."""
        self.quotas = quotas
        self.conversations = conversations
        self._jobs: dict[str, tuple[str, Af3Job]] = {}
        self._created_at: dict[str, float] = {}
        self._request_keys: dict[tuple[str, str], tuple[str, str]] = {}

    def can_submit(self, user_id: str, estimated_minutes: int) -> bool:
        """Check whether the user can reserve estimated GPU time."""
        return self.quotas.available_gpu(user_id) >= estimated_minutes

    def artifacts_for(self, user_id: str) -> list[dict[str, str]]:
        """Collect artifact metadata from the user's simulated jobs."""
        return [
            artifact
            for owner, job in self._jobs.values()
            if owner == user_id
            for artifact in job.artifacts
        ]

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        """Return no bytes because this mock stores artifact metadata only."""
        return None

    def submit(
        self, user_id: str, payload: Af3JobRequest, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Reserve GPU time and create an idempotent queued simulation job.

        Raises:
            Af3IdempotencyConflict: A key was reused with different input.
        """
        fingerprint = af3_request_fingerprint(payload) if idempotency_key else None
        if idempotency_key:
            previous = self._request_keys.get((user_id, idempotency_key))
            if previous:
                if previous[0] != fingerprint:
                    raise Af3IdempotencyConflict
                return self._jobs[previous[1]][1]
        job_id = str(uuid.uuid4())
        self.quotas.reserve_gpu(
            user_id, job_id, payload.estimated_gpu_minutes, run_id=payload.run_id,
        )
        job = Af3Job(
            id=job_id,
            status="queued",
            progress=0,
            estimated_gpu_minutes=payload.estimated_gpu_minutes,
            simulation=True,
            run_id=payload.run_id,
            fold_input=payload.fold_input.model_dump(by_alias=True, exclude_none=True)
            if payload.fold_input else None,
        )
        self._jobs[job_id] = (user_id, job)
        self._created_at[job_id] = monotonic()
        if idempotency_key:
            self._request_keys[(user_id, idempotency_key)] = (fingerprint, job_id)
        if job.run_id:
            self.conversations.append_event(
                user_id,
                job.run_id,
                TaskUpdatedEvent(
                    run_id=job.run_id,
                    data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3", status="queued", progress=0),
                ),
            )
        return job

    def get(self, user_id: str, job_id: str) -> Af3Job | None:
        """Return the job only to its owner."""
        entry = self._jobs.get(job_id)
        if entry is None or entry[0] != user_id:
            return None
        return entry[1]

    def cancel(self, user_id: str, job_id: str) -> Af3Job | None:
        """Cancel an active simulation job and release reserved GPU time."""
        job = self.get(user_id, job_id)
        if job is None:
            return None
        if job.status in {"queued", "running"}:
            self.quotas.release_gpu(user_id, job_id)
            job.status = "cancelled"
            job.gpu_accounting_status = "released"
            if job.run_id:
                self.conversations.append_event(
                    user_id, job.run_id,
                    TaskUpdatedEvent(
                        run_id=job.run_id,
                        data=TaskUpdatedData(job_id=job_id, label="AlphaFold 3",
                                             status="cancelled", progress=job.progress),
                    ),
                )
        return job

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        """List the owner's queued or running jobs for one agent run."""
        return [job_id for job_id, (owner, job) in self._jobs.items()
                if owner == user_id and job.run_id == run_id
                and job.status in {"queued", "running"}]

    def advance(self, duration_seconds: float) -> None:
        """Move elapsed mock jobs through running and completed states."""
        now = monotonic()
        for job_id, (user_id, job) in self._jobs.items():
            elapsed = now - self._created_at[job_id]
            if job.status == "queued" and elapsed >= duration_seconds / 2:
                job.status = "running"
                job.progress = 50
                self.quotas.mark_gpu_running(user_id, job_id)
                if job.run_id:
                    self.conversations.append_event(
                        user_id, job.run_id,
                        TaskUpdatedEvent(
                            run_id=job.run_id,
                            data=TaskUpdatedData(
                                job_id=job_id, label="AlphaFold 3", status="running", progress=50
                            ),
                        ),
                    )
            elif job.status == "running" and elapsed >= duration_seconds:
                actual = min(18, job.estimated_gpu_minutes)
                self.quotas.settle_gpu(user_id, job.id, actual)
                job.status = "completed"
                job.gpu_accounting_status = "settled"
                job.progress = 100
                job.actual_gpu_minutes = actual
                job.artifacts = [
                    {"id": f"artifact-{job.id}", "name": "af3_prediction.cif", "kind": "structure"}
                ]
                if job.run_id:
                    self._complete_run(user_id, job)

    def _complete_run(self, user_id: str, job: Af3Job) -> None:
        """Publish task, usage, artifact, and final reply events for a job."""
        run_id = job.run_id
        assert run_id is not None
        self.conversations.append_event(
            user_id,
            run_id,
            TaskUpdatedEvent(
                run_id=run_id, data=TaskUpdatedData(job_id=job.id, label="AlphaFold 3", status="completed", progress=100)
            ),
        )
        self.conversations.append_event(
            user_id,
            run_id,
            UsageUpdatedEvent(
                run_id=run_id,
                data=UsageUpdatedData(gpu_remaining=self.quotas.usage_for(user_id).gpu.remaining),
            ),
        )
        artifact = job.artifacts[0]
        self.conversations.append_event(
            user_id,
            run_id,
            ArtifactCreatedEvent(
                run_id=run_id,
                data=ArtifactCreatedData(
                    artifact_id=artifact["id"], name=artifact["name"], kind=artifact["kind"]
                ),
            ),
        )
        answer = "AF3 模拟任务已完成，结构文件已生成。"
        self.conversations.append_event(
            user_id, run_id, MessageDeltaEvent(run_id=run_id, data=MessageDeltaData(delta=answer))
        )
        self.conversations.add_assistant_reply(user_id, run_id, answer)
        self.conversations.append_event(
            user_id, run_id, RunCompletedEvent(run_id=run_id, data=RunCompletedData())
        )
