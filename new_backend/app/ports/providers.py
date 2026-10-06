from typing import Literal, Protocol

from app.contracts.capabilities import Af3Job, Af3JobRequest, McpInvokeResult, McpTool
from app.contracts.models import UserIdentity


class ProviderUnavailable(Exception):
    """An optional external capability cannot currently serve requests."""


class IdentityTransportUnavailable(ProviderUnavailable):
    """Identity transport failed with a known request-delivery boundary."""

    def __init__(self, delivery: Literal["not_sent", "unknown"]) -> None:
        super().__init__("Identity provider transport unavailable")
        self.delivery = delivery


class IdentityProvider(Protocol):
    """Verify user identity without exposing the provider to API handlers."""

    async def verify(self, access_token: str) -> UserIdentity | None:
        """Resolve a bearer token to an identity or reject it as unknown."""
        ...


class McpProvider(Protocol):
    """Expose and invoke the currently allowed MCP tool catalog."""

    def tools(self) -> list[McpTool]:
        """Return MCP tools currently available to this provider."""
        ...

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Invoke a named tool with validated arguments, if available."""
        ...


class DiscoverableMcpProvider(McpProvider, Protocol):
    """Refresh a remote MCP tool catalog before exposing it to callers."""

    async def discover(self) -> list[McpTool]:
        """Fetch the remote catalog and return its allowed tools."""
        ...


class Af3Provider(Protocol):
    """Submit and inspect AF3 jobs behind one mock or live port."""

    def can_submit(self, user_id: str, estimated_minutes: int) -> bool:
        """Check whether a user has enough GPU minutes for a new job."""
        ...

    def submit(
        self, user_id: str, payload: Af3JobRequest, idempotency_key: str | None = None,
    ) -> Af3Job:
        """Submit or reuse an AF3 job for an owned user request."""
        ...

    def get(self, user_id: str, job_id: str) -> Af3Job | None:
        """Read an AF3 job visible to its owner."""
        ...

    def cancel(self, user_id: str, job_id: str) -> Af3Job | None:
        """Cancel an owned AF3 job when it is still active."""
        ...

    def active_job_ids_for_run(self, user_id: str, run_id: str) -> list[str]:
        """List active AF3 jobs attached to an owned Agent Run."""
        ...

    def artifacts_for(self, user_id: str, session_id: str | None = None) -> list[dict[str, str]]:
        """List AF3 artifacts owned by a user, optionally from one session."""
        ...

    def artifact_bytes_for(self, user_id: str, artifact_id: str) -> tuple[str, bytes] | None:
        """Return downloadable artifact name and bytes when owned and ready."""
        ...
