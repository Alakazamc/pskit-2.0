import asyncio
import secrets
import uuid

from app.domain.internal_auth import WorkspaceToolTokenCodec
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.workspace_access import WorkspaceAccessPolicy
from app.services.agent_context import AgentContextMixin
from app.services.agent_events import project_pi_event
from app.services.agent_executor import InitialTurnMixin
from app.services.agent_resume import ResumeTurnMixin
from app.services.agent_scheduler import AgentSchedulerMixin
from app.services.agent_workspace import WorkspaceTurnMixin
from app.services.session_locks import SessionLockRegistry


class AgentService(
    AgentSchedulerMixin, AgentContextMixin, WorkspaceTurnMixin, InitialTurnMixin, ResumeTurnMixin
):
    """Schedule durable Pi Runs, project events, and resume completed jobs."""

    @staticmethod
    def _reported_tokens(event: dict) -> int:
        """Extract positive assistant Token usage from a Pi message-end event."""
        if event.get("type") != "message_end":
            return 0
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        count = usage.get("totalTokens")
        return (
            count if message.get("role") == "assistant" and type(count) is int and count > 0 else 0
        )

    def __init__(
        self,
        store: PersistentConversationStore,
        runner,
        internal_api_url: str,
        mock_af3_seconds: float,
        resume_retry_seconds: float,
        model_gateway_api_key: str | None = None,
        requires_gateway_key: bool = False,
        model_gateway_proxy_enabled: bool = False,
        mcp_tools: list | None = None,
        af3_executor: str = "mock",
        af3_queue_timeout_seconds: int = 3600,
        af3_execution_timeout_seconds: int = 21600,
        max_active_runs: int = 4,
        max_active_runs_per_user: int = 2,
        tool_token_secret: bytes | None = None,
        mcp_tool_calls: McpToolCallStore | None = None,
        workspace_access: WorkspaceAccessPolicy | None = None,
    ) -> None:
        """Bind the durable store, Pi runner, tool gateway, and scheduler limits.

        Args:
            store: Persistent conversation and quota store.
            runner: Pi RPC adapter used for turns and resumes.
            internal_api_url: Python URL available to Pi extensions.
            mock_af3_seconds: Simulated AF3 job duration.
            resume_retry_seconds: Base retry delay for safe resumes.
            model_gateway_api_key: Server-owned model gateway credential.
            requires_gateway_key: Whether execution requires that credential.
            model_gateway_proxy_enabled: Route Pi model calls through Python.
            mcp_tools: Tools currently discoverable by the Agent.
            af3_executor: Selected mock, callback, or disabled AF3 mode.
            af3_queue_timeout_seconds: Deadline for a worker to claim a job.
            af3_execution_timeout_seconds: Maximum running AF3 duration.
            max_active_runs: Global Pi Run concurrency limit.
            max_active_runs_per_user: Per-user Pi Run concurrency limit.
            tool_token_secret: Shared secret for internal tool request tokens.
            mcp_tool_calls: Durable MCP tool result ledger.
        """
        self.store = store
        self.runner = runner
        self.internal_api_url = internal_api_url
        self.mock_af3_seconds = mock_af3_seconds
        self.resume_retry_seconds = resume_retry_seconds
        self.model_gateway_api_key = model_gateway_api_key
        self.requires_gateway_key = requires_gateway_key
        self.model_gateway_proxy_enabled = model_gateway_proxy_enabled
        self.mcp_tools = mcp_tools or []
        self.af3_executor = af3_executor
        self.af3_queue_timeout_seconds = af3_queue_timeout_seconds
        self.af3_execution_timeout_seconds = af3_execution_timeout_seconds
        self.max_active_runs = max_active_runs
        self.max_active_runs_per_user = max_active_runs_per_user
        self._tool_secret = tool_token_secret or secrets.token_bytes(32)
        self._workspace_tool_tokens = WorkspaceToolTokenCodec(self._tool_secret)
        self.mcp_tool_calls = mcp_tool_calls
        self.workspace_access = workspace_access
        self.catalog = None
        self.workspace_transfer = None
        self.workspace_sandbox_provider = None
        self.workspace_sandbox_store = None
        self.workspace_attempts = None
        self.compute_leases = None
        self.agent_compute_tools = None
        self._instance_id = uuid.uuid4().hex
        self._locks = SessionLockRegistry()
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._scheduler: asyncio.Task | None = None

    def has_model_token(self, user_id: str) -> bool:
        """Check service-level gateway credentials when live Pi requires them."""
        return not self.requires_gateway_key or bool(self.model_gateway_api_key)

    def _record_progress_event(self, user_id: str, run_id: str, event: dict) -> None:
        """Compatibility synchronous projection entry; runtime uses the delta writer."""
        for projected in project_pi_event(run_id, event):
            self.store.append_event(user_id, run_id, projected)
