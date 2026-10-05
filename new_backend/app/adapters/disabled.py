from app.contracts.capabilities import Af3Job, Af3JobRequest, McpInvokeResult, McpTool
from app.ports.providers import ProviderUnavailable


class DisabledMcp:
    """Represent an MCP capability that has not been configured."""

    def tools(self) -> list[McpTool]:
        """Expose no tools while MCP is disabled."""
        return []

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Reject every invocation when MCP has no configured executor.

        Raises:
            ProviderUnavailable: MCP execution is disabled.
        """
        raise ProviderUnavailable("MCP is not configured")


class DisabledAf3:
    """Represent AF3 compute that has not been deployed or enabled."""

    def can_submit(self, user_id: str, estimated_minutes: int) -> bool:
        """Reject all AF3 submissions while compute is disabled."""
        return False

    def submit(
        self, user_id: str, payload: Af3JobRequest, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Reject an AF3 submission when no executor is configured.

        Raises:
            ProviderUnavailable: AF3 execution is disabled.
        """
        raise ProviderUnavailable("AF3 is not configured")

    def get(self, user_id: str, job_id: str) -> Af3Job | None:
        """Return no job while AF3 is disabled."""
        return None

    def cancel(self, user_id: str, job_id: str) -> Af3Job | None:
        """Return no cancellation result while AF3 is disabled."""
        return None

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        """Return no active AF3 jobs while execution is disabled."""
        return []

    def artifacts_for(self, user_id: str, session_id: str | None = None) -> list[dict[str, str]]:
        """Return no AF3 artifacts while execution is disabled."""
        return []

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        """Return no downloadable AF3 artifact while execution is disabled."""
        return None
