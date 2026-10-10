"""Disabled provider implementations used for explicit fail-closed modes."""

from app.contracts.capabilities import Af3Job, Af3JobRequest, McpInvokeResult, McpTool
from app.ports.providers import ProviderUnavailable

from .workspace_sandbox import DisabledWorkspaceSandboxProvider

__all__ = ["DisabledAf3", "DisabledMcp", "DisabledWorkspaceSandboxProvider"]


class DisabledMcp:
    """Represent an MCP capability that has not been configured."""

    def tools(self) -> list[McpTool]:
        """Expose no tools while MCP is disabled."""
        return []

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Reject every invocation when MCP has no configured executor."""
        raise ProviderUnavailable("MCP is not configured")


class DisabledAf3:
    """Represent AF3 compute that has not been deployed or enabled."""

    def can_submit(self, user_id: str, estimated_minutes: int) -> bool:
        """Reject all AF3 submissions while compute is disabled."""
        return False

    def submit(
        self, user_id: str, payload: Af3JobRequest, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Reject an AF3 submission when no executor is configured."""
        raise ProviderUnavailable("AF3 is not configured")

    def get(self, user_id: str, job_id: str) -> Af3Job | None:
        return None

    def cancel(self, user_id: str, job_id: str) -> Af3Job | None:
        return None

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        return []

    def artifacts_for(self, user_id: str, session_id: str | None = None) -> list[dict[str, str]]:
        return []

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        return None
