import uuid
from dataclasses import replace

from app.adapters.live.pi_rpc import PiRpcError
from app.contracts.conversation import (
    ArtifactCreatedData,
    ArtifactCreatedEvent,
)
from app.ports.workspace_sandbox import WorkspaceCapabilities, WorkspaceProviderError


class WorkspaceTurnMixin:
    """Internal AgentService responsibility; the facade preserves its API."""

    async def _collect_workspace(self, user_id, session_id, run_id, attempt_id):
        if not (await self._db_call(self.store.owns_lease, run_id, self._instance_id)):
            return
        for artifact in await self.workspace_transfer.collect(user_id, session_id, attempt_id):
            (
                await self._db_call(
                    self.store.append_event,
                    user_id,
                    run_id,
                    ArtifactCreatedEvent(
                        run_id=run_id,
                        data=ArtifactCreatedData(
                            artifact_id=artifact.id,
                            name=artifact.name,
                            kind=artifact.kind,
                        ),
                    ),
                )
            )

    async def _open_workspace_attempt(
        self,
        user_id: str,
        session_id: str,
        run_id: str,
        *,
        required: bool,
    ) -> tuple[str, WorkspaceCapabilities] | None:
        """Prepare one fenced Pi-turn workspace without blocking plain chat outages."""
        if (
            self.workspace_sandbox_provider is None
            or self.workspace_sandbox_store is None
            or self.workspace_attempts is None
            or self.workspace_access is None
        ):
            return None
        if not await self._db_call(self.workspace_access.files_allowed, user_id):
            return None
        try:
            capabilities = await self.workspace_sandbox_provider.capabilities()
            if not await self._db_call(
                self.workspace_access.commands_allowed, user_id, capabilities
            ):
                capabilities = replace(
                    capabilities,
                    command_execution=False,
                    command_events=False,
                    cancellation=False,
                    metrics=False,
                )
            if not capabilities.file_access:
                if required:
                    raise PiRpcError(
                        "Workspace file access is unavailable",
                        code="WORKSPACE_UNAVAILABLE",
                    )
                return None
            await self.workspace_sandbox_provider.ensure_user(user_id)
            attempt_id = "workspace-" + uuid.uuid4().hex
            lease = await self._db_call(
                self.workspace_sandbox_store.acquire_lease,
                attempt_id=attempt_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                lease_seconds=900,
            )
            (
                await self._db_call(
                    self.workspace_attempts.create,
                    attempt_id=attempt_id,
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    fencing_token=lease.fencing_token,
                    reserved_cpu_core_ms=0,
                )
            )
            return attempt_id, capabilities
        except WorkspaceProviderError as exc:
            if required:
                raise PiRpcError(str(exc), code=exc.code.value, retryable=exc.retryable) from exc
            return None

    async def _close_workspace_attempt(
        self,
        user_id: str,
        session_id: str,
        run_id: str,
        attempt_id: str,
        *,
        succeeded: bool,
    ) -> None:
        """Release an idle attempt or preserve unconfirmed command state."""
        attempt = (
            (await self._db_call(self.workspace_attempts.get, attempt_id))
            if self.workspace_attempts
            else None
        )
        if attempt is None:
            return
        if attempt.status == "queued":
            status = "completed" if succeeded else "failed"
            (
                await self._db_call(
                    self.workspace_attempts.finish,
                    attempt_id,
                    attempt.fencing_token,
                    status=status,
                    wall_ms=0,
                    cpu_core_ms=0,
                    peak_memory_bytes=0,
                    exit_code=0 if succeeded else None,
                    output_truncated=False,
                )
            )
            (
                await self._db_call(
                    self.workspace_sandbox_store.release_after_exit,
                    attempt_id,
                    attempt.fencing_token,
                    terminal_state=status,
                    exit_code=0 if succeeded else None,
                )
            )
            attempt = await self._db_call(self.workspace_attempts.get, attempt_id)
        elif attempt.status in {"running", "cancelling"}:
            (
                await self._db_call(
                    self.workspace_attempts.mark_unknown,
                    attempt_id,
                    attempt.fencing_token,
                    "WORKSPACE_PROCESS_UNCONFIRMED",
                )
            )
            return
        if attempt is not None and attempt.status in {"completed", "failed", "cancelled"}:
            await self._collect_workspace(user_id, session_id, run_id, attempt_id)

    async def _close_workspace_attempt_safely(
        self,
        user_id: str,
        session_id: str,
        run_id: str,
        workspace_turn: tuple[str, WorkspaceCapabilities] | None,
        *,
        succeeded: bool,
    ) -> None:
        """Best-effort cleanup which never masks the original Pi outcome."""
        if workspace_turn is None:
            return
        try:
            await self._close_workspace_attempt(
                user_id,
                session_id,
                run_id,
                workspace_turn[0],
                succeeded=succeeded,
            )
        except Exception:  # noqa: BLE001 - cleanup must not replace the Pi failure.
            return
