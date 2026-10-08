"""Provider-neutral contracts for isolated user workspaces.

The control plane depends only on these DTOs and protocols.  OpenSandbox
SDK objects, credentials, container settings and transport exceptions must
not cross this boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class WorkspaceErrorCode(StrEnum):
    """Stable failure classes exposed to control-plane services."""

    UNAVAILABLE = "WORKSPACE_UNAVAILABLE"
    UNSAFE_RUNTIME = "WORKSPACE_UNSAFE_RUNTIME"
    CONFLICT = "WORKSPACE_CONFLICT"
    TIMEOUT = "WORKSPACE_TIMEOUT"
    CAPACITY = "WORKSPACE_CAPACITY"
    INVALID_PATH = "WORKSPACE_INVALID_PATH"
    CANCEL_UNCONFIRMED = "WORKSPACE_CANCEL_UNCONFIRMED"


class WorkspaceProviderError(RuntimeError):
    """A sanitized workspace failure safe to map into an API error."""

    def __init__(
        self,
        code: WorkspaceErrorCode,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_id = request_id


class WorkspaceUnavailable(WorkspaceProviderError):
    """The selected provider cannot currently serve workspace requests."""

    def __init__(self, message: str = "Workspace execution is unavailable") -> None:
        super().__init__(WorkspaceErrorCode.UNAVAILABLE, message, retryable=True)


class WorkspaceUnsafeRuntime(WorkspaceProviderError):
    """The provider could not prove the configured isolation boundary."""

    def __init__(self, message: str = "Workspace runtime safety could not be verified") -> None:
        super().__init__(WorkspaceErrorCode.UNSAFE_RUNTIME, message)


class WorkspaceConflict(WorkspaceProviderError):
    """A fencing token, revision, or lifecycle transition is stale."""

    def __init__(self, message: str = "Workspace state changed concurrently") -> None:
        super().__init__(WorkspaceErrorCode.CONFLICT, message, retryable=True)


class WorkspaceTimeout(WorkspaceProviderError):
    """A provider operation exceeded its configured deadline."""

    def __init__(self, message: str = "Workspace operation timed out") -> None:
        super().__init__(WorkspaceErrorCode.TIMEOUT, message, retryable=True)


class WorkspaceCapacityExceeded(WorkspaceProviderError):
    """The provider cannot admit more work within its capacity limits."""

    def __init__(self, message: str = "Workspace capacity is exhausted") -> None:
        super().__init__(WorkspaceErrorCode.CAPACITY, message, retryable=True)


class WorkspaceInvalidPath(WorkspaceProviderError):
    """A logical path escaped the owned Session workspace."""

    def __init__(self, message: str = "Workspace path is invalid") -> None:
        super().__init__(WorkspaceErrorCode.INVALID_PATH, message)


class WorkspaceReadinessState(StrEnum):
    """Operational status used by readiness and rollout gates."""

    DISABLED = "disabled"
    READY = "ready"
    DEGRADED = "degraded"
    UNSAFE = "unsafe"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilities:
    """Verified capabilities, never promises inferred from configuration."""

    provider: str
    runtime: str | None = None
    file_access: bool = False
    command_execution: bool = False
    command_events: bool = False
    cancellation: bool = False
    metrics: bool = False
    persistent_volume: bool = False
    session_mount_namespace: bool = False
    uid: int | None = None
    gid: int | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceReadiness:
    """Sanitized provider health returned to application readiness."""

    state: WorkspaceReadinessState
    code: str
    capabilities: WorkspaceCapabilities
    detail: str = ""

    @property
    def ready(self) -> bool:
        """Return true only for a positively verified provider."""
        return self.state is WorkspaceReadinessState.READY


@dataclass(frozen=True, slots=True)
class WorkspaceSandbox:
    """One replaceable runtime instance attached to a durable user volume."""

    user_id: str
    sandbox_id: str
    volume_id: str
    image_digest: str
    provider_revision: int
    lifecycle_state: str


@dataclass(frozen=True, slots=True)
class WorkspaceFileEntry:
    """Metadata for one path below the current Session root."""

    path: str
    kind: str
    size: int
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceFileChunk:
    """A bounded byte range read from one owned workspace file."""

    path: str
    content: bytes
    offset: int
    eof: bool
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceWriteResult:
    """Metadata produced by an atomic file write."""

    path: str
    size: int
    revision: str


@dataclass(frozen=True, slots=True)
class WorkspaceCommandRequest:
    """A command admitted by PSKit for one Session attempt."""

    session_id: str
    attempt_id: str
    argv: tuple[str, ...]
    cwd: str = "attempts"
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_048_576


@dataclass(frozen=True, slots=True)
class WorkspaceCommandHandle:
    """Opaque provider process identity scoped to one owned sandbox."""

    sandbox_id: str
    attempt_id: str
    process_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceCommandEvent:
    """A bounded incremental command event."""

    sequence: int
    type: str
    data: bytes = b""
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceCommandStatus:
    """Authoritative process state from the execution supervisor."""

    state: str
    exit_code: int | None = None
    termination_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceMetrics:
    """Provider observations used by PSKit's existing quota ledger."""

    wall_ms: int
    cpu_core_ms: int | None = None
    peak_memory_bytes: int | None = None


class WorkspaceFilePort(Protocol):
    """Bounded file operations under `/workspace/<session_id>/`."""

    async def stat(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
    ) -> WorkspaceFileEntry: ...

    async def list(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
    ) -> tuple[WorkspaceFileEntry, ...]: ...

    async def read(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
        *,
        offset: int = 0,
        limit: int = 1_048_576,
    ) -> WorkspaceFileChunk: ...

    async def write(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
        content: bytes,
        *,
        expected_revision: str | None = None,
    ) -> WorkspaceWriteResult: ...


class WorkspaceCommandPort(Protocol):
    """Process lifecycle operations inside a verified Session namespace."""

    async def start(
        self,
        sandbox: WorkspaceSandbox,
        request: WorkspaceCommandRequest,
    ) -> WorkspaceCommandHandle: ...

    def events(self, handle: WorkspaceCommandHandle) -> AsyncIterator[WorkspaceCommandEvent]: ...

    async def status(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus: ...

    async def cancel(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus: ...

    async def metrics(self, handle: WorkspaceCommandHandle) -> WorkspaceMetrics: ...


class WorkspaceSandboxProvider(Protocol):
    """Lifecycle and execution boundary for one user's isolated workspace."""

    files: WorkspaceFilePort
    commands: WorkspaceCommandPort

    async def ensure_user(self, user_id: str) -> WorkspaceSandbox: ...

    async def capabilities(self) -> WorkspaceCapabilities: ...

    async def readiness(self) -> WorkspaceReadiness: ...

    async def stop_user(
        self,
        user_id: str,
        expected_revision: int | None = None,
    ) -> None: ...

    async def replace_user(
        self,
        user_id: str,
        expected_revision: int,
    ) -> WorkspaceSandbox: ...
