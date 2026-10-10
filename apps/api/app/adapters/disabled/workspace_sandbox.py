"""Explicitly disabled workspace provider."""

from collections.abc import AsyncIterator

from app.ports.workspace_sandbox import (
    WorkspaceCapabilities,
    WorkspaceCommandEvent,
    WorkspaceCommandHandle,
    WorkspaceCommandRequest,
    WorkspaceCommandStatus,
    WorkspaceFileChunk,
    WorkspaceFileEntry,
    WorkspaceMetrics,
    WorkspaceReadiness,
    WorkspaceReadinessState,
    WorkspaceSandbox,
    WorkspaceUnavailable,
    WorkspaceWriteResult,
)


class _DisabledFiles:
    @staticmethod
    def _raise() -> None:
        raise WorkspaceUnavailable()

    async def stat(self, sandbox, session_id, path) -> WorkspaceFileEntry:
        self._raise()

    async def list(self, sandbox, session_id, path) -> tuple[WorkspaceFileEntry, ...]:
        self._raise()

    async def read(
        self,
        sandbox,
        session_id,
        path,
        *,
        offset=0,
        limit=1_048_576,
    ) -> WorkspaceFileChunk:
        self._raise()

    async def write(
        self,
        sandbox,
        session_id,
        path,
        content,
        *,
        expected_revision=None,
    ) -> WorkspaceWriteResult:
        self._raise()


class _DisabledCommands:
    async def start(
        self,
        sandbox: WorkspaceSandbox,
        request: WorkspaceCommandRequest,
    ) -> WorkspaceCommandHandle:
        raise WorkspaceUnavailable()

    async def _unavailable_events(
        self,
        handle: WorkspaceCommandHandle,
    ) -> AsyncIterator[WorkspaceCommandEvent]:
        raise WorkspaceUnavailable()
        yield  # pragma: no cover - keeps this an async iterator without executing

    def events(self, handle: WorkspaceCommandHandle) -> AsyncIterator[WorkspaceCommandEvent]:
        return self._unavailable_events(handle)

    async def status(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus:
        raise WorkspaceUnavailable()

    async def cancel(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus:
        raise WorkspaceUnavailable()

    async def metrics(self, handle: WorkspaceCommandHandle) -> WorkspaceMetrics:
        raise WorkspaceUnavailable()


class DisabledWorkspaceSandboxProvider:
    """Keep ordinary chat available while every workspace call fails closed."""

    def __init__(self) -> None:
        self.files = _DisabledFiles()
        self.commands = _DisabledCommands()
        self._capabilities = WorkspaceCapabilities(provider="disabled")

    async def ensure_user(self, user_id: str) -> WorkspaceSandbox:
        raise WorkspaceUnavailable()

    async def capabilities(self) -> WorkspaceCapabilities:
        return self._capabilities

    async def readiness(self) -> WorkspaceReadiness:
        return WorkspaceReadiness(
            state=WorkspaceReadinessState.DISABLED,
            code="WORKSPACE_DISABLED",
            detail="Workspace execution is disabled",
            capabilities=self._capabilities,
        )

    async def stop_user(
        self,
        user_id: str,
        expected_revision: int | None = None,
    ) -> None:
        raise WorkspaceUnavailable()

    async def replace_user(self, user_id: str, expected_revision: int) -> WorkspaceSandbox:
        raise WorkspaceUnavailable()
