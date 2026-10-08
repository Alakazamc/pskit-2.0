"""Metered command execution through the isolated workspace provider."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.contracts.workspace_tools import WorkspaceToolEvent
from app.domain.workspace_attempts import (
    WorkspaceAttempt,
    WorkspaceAttemptStore,
    WorkspaceCpuQuotaExceeded,
)
from app.domain.workspace_paths import WorkspacePathPolicy
from app.ports.workspace_sandbox import (
    WorkspaceCommandHandle,
    WorkspaceCommandRequest,
    WorkspaceConflict,
    WorkspaceProviderError,
    WorkspaceSandboxProvider,
    WorkspaceUnsafeRuntime,
)
from app.services.workspace_files import WorkspaceOperationContext


class WorkspaceCommands:
    """Apply capability, quota, attempt and fencing policy around provider commands."""

    def __init__(
        self,
        provider: WorkspaceSandboxProvider,
        attempts: WorkspaceAttemptStore,
        *,
        cpu_daily_limit_ms: int,
        cpu_millicores: int,
    ) -> None:
        self.provider = provider
        self.attempts = attempts
        self.cpu_daily_limit_ms = cpu_daily_limit_ms
        self.cpu_millicores = cpu_millicores
        self.paths = WorkspacePathPolicy()
        self._handles: dict[str, WorkspaceCommandHandle] = {}

    async def start(
        self,
        context: WorkspaceOperationContext,
        *,
        run_id: str,
        argv: tuple[str, ...],
        cwd: str | None,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> str:
        capabilities = await self.provider.capabilities()
        if not (
            capabilities.runtime == "runsc"
            and capabilities.command_execution
            and capabilities.command_events
            and capabilities.session_mount_namespace
        ):
            raise WorkspaceUnsafeRuntime("Workspace command execution is unavailable")
        attempt = self._owned(context, run_id)
        command_cwd = cwd or f"attempts/{context.attempt_id}"
        resolved_cwd = self.paths.logical(context.session_id, command_cwd, writable=True)
        parts = resolved_cwd.logical.split("/")
        if parts[0] in {"attempts", "artifacts"} and (
            len(parts) < 2 or parts[1] != context.attempt_id
        ):
            raise WorkspaceConflict("Workspace command directory is out of scope")
        reservation = max(1, int(timeout_seconds * self.cpu_millicores))
        self.attempts.reserve_cpu(
            attempt.attempt_id,
            attempt.fencing_token,
            reservation,
            self.cpu_daily_limit_ms,
        )
        sandbox = await self.provider.ensure_user(context.user_id)
        try:
            handle = await self.provider.commands.start(
                sandbox,
                WorkspaceCommandRequest(
                    session_id=context.session_id,
                    attempt_id=context.attempt_id,
                    run_id=run_id,
                    argv=argv,
                    cwd=resolved_cwd.logical,
                    env={"HOME": "/workspace/work", "TMPDIR": "/workspace/work/.tmp"},
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                ),
            )
            self.attempts.mark_running(
                attempt.attempt_id, attempt.fencing_token, handle.process_id
            )
        except WorkspaceProviderError as exc:
            self.attempts.mark_unknown(attempt.attempt_id, attempt.fencing_token, exc.code.value)
            raise
        self._handles[context.attempt_id] = handle
        return context.attempt_id

    async def stream(
        self,
        context: WorkspaceOperationContext,
        *,
        run_id: str,
        process_id: str,
    ) -> AsyncIterator[WorkspaceToolEvent]:
        attempt = self._owned(context, run_id)
        handle = self._handle(attempt, process_id)
        sequence = 1
        output_truncated = False
        yield WorkspaceToolEvent(sequence=sequence, type="started")
        try:
            async for event in self.provider.commands.events(handle):
                sequence += 1
                if event.type == "exit":
                    metrics = await self.provider.commands.metrics(handle)
                    sequence += 1
                    yield WorkspaceToolEvent(
                        sequence=sequence,
                        type="usage",
                        wall_ms=metrics.wall_ms,
                        cpu_core_ms=metrics.cpu_core_ms,
                        peak_memory_bytes=metrics.peak_memory_bytes,
                    )
                    terminal = await self.provider.commands.status(handle)
                    status = terminal.state if terminal.state in {
                        "completed", "failed", "cancelled"
                    } else "failed"
                    self.attempts.finish(
                        attempt.attempt_id,
                        attempt.fencing_token,
                        status=status,
                        wall_ms=metrics.wall_ms,
                        cpu_core_ms=metrics.cpu_core_ms,
                        peak_memory_bytes=metrics.peak_memory_bytes,
                        exit_code=terminal.exit_code,
                        output_truncated=output_truncated,
                    )
                    yield WorkspaceToolEvent(
                        sequence=sequence + 1,
                        type="exit",
                        exit_code=terminal.exit_code,
                    )
                    return
                output_truncated = output_truncated or event.truncated
                yield WorkspaceToolEvent(
                    sequence=sequence,
                    type="stdout" if event.type == "output" else "error",
                    data=event.data.decode("utf-8", errors="replace"),
                    truncated=event.truncated,
                )
        except WorkspaceProviderError as exc:
            self.attempts.mark_unknown(attempt.attempt_id, attempt.fencing_token, exc.code.value)
            raise

    async def status(
        self,
        context: WorkspaceOperationContext,
        *,
        run_id: str,
        process_id: str,
    ) -> tuple[str, int | None, bool]:
        attempt = self._owned(context, run_id)
        if attempt.status in {"completed", "failed", "cancelled", "unknown"}:
            return attempt.status, attempt.exit_code, attempt.status != "unknown"
        if attempt.provider_process_id is None:
            return attempt.status, None, False
        handle = self._handle(attempt, process_id)
        status = await self.provider.commands.status(handle)
        if status.termination_confirmed and status.state in {"completed", "failed", "cancelled"}:
            metrics = await self.provider.commands.metrics(handle)
            self.attempts.finish(
                attempt.attempt_id,
                attempt.fencing_token,
                status=status.state,
                wall_ms=metrics.wall_ms,
                cpu_core_ms=metrics.cpu_core_ms,
                peak_memory_bytes=metrics.peak_memory_bytes,
                exit_code=status.exit_code,
                output_truncated=False,
            )
        return status.state, status.exit_code, status.termination_confirmed

    async def cancel(
        self,
        context: WorkspaceOperationContext,
        *,
        run_id: str,
        process_id: str,
    ) -> tuple[str, int | None, bool]:
        attempt = self._owned(context, run_id)
        handle = self._handle(attempt, process_id)
        self.attempts.begin_cancel(attempt.attempt_id, attempt.fencing_token)
        try:
            terminal = await self.provider.commands.cancel(handle)
            metrics = await self.provider.commands.metrics(handle)
        except WorkspaceProviderError as exc:
            self.attempts.mark_unknown(attempt.attempt_id, attempt.fencing_token, exc.code.value)
            raise
        if not terminal.termination_confirmed or terminal.state != "cancelled":
            self.attempts.mark_unknown(
                attempt.attempt_id,
                attempt.fencing_token,
                "WORKSPACE_CANCEL_UNCONFIRMED",
            )
            raise WorkspaceConflict("Workspace cancellation was not confirmed")
        self.attempts.finish(
            attempt.attempt_id,
            attempt.fencing_token,
            status="cancelled",
            wall_ms=metrics.wall_ms,
            cpu_core_ms=metrics.cpu_core_ms,
            peak_memory_bytes=metrics.peak_memory_bytes,
            exit_code=terminal.exit_code,
            output_truncated=False,
        )
        return terminal.state, terminal.exit_code, True

    def _owned(self, context: WorkspaceOperationContext, run_id: str) -> WorkspaceAttempt:
        attempt = self.attempts.get(context.attempt_id)
        if (
            attempt is None
            or attempt.user_id != context.user_id
            or attempt.session_id != context.session_id
            or attempt.run_id != run_id
        ):
            raise WorkspaceConflict("Workspace attempt scope changed")
        return attempt

    def _handle(self, attempt: WorkspaceAttempt, public_process_id: str) -> WorkspaceCommandHandle:
        if public_process_id != attempt.attempt_id or attempt.provider_process_id is None:
            raise WorkspaceConflict("Workspace process is unavailable")
        handle = self._handles.get(attempt.attempt_id)
        if handle is not None:
            return handle
        raise WorkspaceConflict("Workspace process state is unavailable")


__all__ = ["WorkspaceCommands", "WorkspaceCpuQuotaExceeded"]
