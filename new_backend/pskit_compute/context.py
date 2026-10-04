"""Optional progress/cancellation hooks, separate from model input and measurement."""

from datetime import UTC, datetime


class ExecutionContext:
    def __init__(self, grant, *, cancelled=lambda: False, progress=lambda value: None):
        self.grant = grant
        self._cancelled = cancelled
        self._progress = progress

    def check_cancelled(self):
        """Cooperative check. This cannot stop arbitrary remote CUDA execution."""
        if self._cancelled() or datetime.now(UTC) >= self.grant.stop_at:
            raise RuntimeError("COMPUTE_CANCEL_REQUESTED")

    def report_progress(self, progress: int):
        if type(progress) is not int or not 0 <= progress <= 100:
            raise ValueError("Progress must be an integer percentage")
        self._progress(progress)
