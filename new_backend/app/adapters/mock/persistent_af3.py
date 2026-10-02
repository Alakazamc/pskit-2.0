from app.contracts.capabilities import Af3Job, Af3JobRequest
from app.domain.persistent_conversation import PersistentConversationStore


class PersistentMockAf3:
    def __init__(self, store: PersistentConversationStore, *, simulation: bool = True) -> None:
        """Back AF3 jobs with SQLite and label simulated jobs accordingly.

        Args:
            store: Persistent conversation and compute job store.
            simulation: Whether submitted jobs are marked as simulated.
        """
        self.store = store
        self.simulation = simulation

    def can_submit(self, user_id: str, estimated_minutes: int) -> bool:
        """Check the user's remaining daily GPU minutes."""
        return self.store.available_gpu(user_id) >= estimated_minutes

    def submit(
        self, user_id: str, payload: Af3JobRequest, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Persist an AF3 job and reserve its estimated GPU minutes."""
        return self.store.create_af3_job(
            user_id, payload.estimated_gpu_minutes, payload.run_id,
            fold_input=payload.fold_input,
            idempotency_key=idempotency_key,
            simulation=self.simulation,
        )

    def get(self, user_id: str, job_id: str) -> Af3Job | None:
        """Find a job only when it belongs to the requesting user."""
        return self.store.get_af3_job(user_id, job_id)

    def cancel(self, user_id: str, job_id: str) -> Af3Job | None:
        """Cancel a user's active job and release its reservation."""
        return self.store.cancel_af3_job(user_id, job_id)

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        """List unfinished jobs associated with the user's agent run."""
        return self.store.active_job_ids_for_run(user_id, run_id)

    def artifacts_for(self, user_id: str) -> list[dict[str, str]]:
        """List AF3 artifact metadata visible to a user."""
        return self.store.af3_artifacts_for(user_id)

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        """Read a user's stored AF3 artifact, if one exists."""
        return self.store.artifact_bytes_for(user_id, artifact_id)
