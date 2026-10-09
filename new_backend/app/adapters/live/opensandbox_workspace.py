"""Fail-closed OpenSandbox workspace provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
import secrets
import shlex
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.adapters.live.opensandbox_compat import (
    CompatFileInfo,
    OpenSandboxCompat,
    OpenSandboxCreateSpec,
)
from app.config import Settings
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.ports.workspace_sandbox import (
    WorkspaceCapabilities,
    WorkspaceCommandEvent,
    WorkspaceCommandHandle,
    WorkspaceCommandRequest,
    WorkspaceCommandStatus,
    WorkspaceConflict,
    WorkspaceErrorCode,
    WorkspaceFileChunk,
    WorkspaceFileEntry,
    WorkspaceInvalidPath,
    WorkspaceMetrics,
    WorkspaceProviderError,
    WorkspaceReadiness,
    WorkspaceReadinessState,
    WorkspaceSandbox,
    WorkspaceUnavailable,
    WorkspaceUnsafeRuntime,
    WorkspaceWriteResult,
)
from app.services.workspace_sandbox_lifecycle import WorkspaceSandboxLifecycle

logger = logging.getLogger("pskit.workspace.provider")

_IDENTIFIER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_RESERVED_ENV_PREFIXES = (
    "OPEN_SANDBOX",
    "PSKIT_",
    "RESEARCH_AGENT_",
    "SUPABASE_",
    "DATABASE_",
    "LITELLM_",
    "AWS_",
    "GOOGLE_",
    "AZURE_",
)
_WORKSPACE_ALIAS_ROOT = "/data/pskit-workspace-root"


@dataclass(slots=True)
class _Process:
    sandbox: WorkspaceSandbox
    request: WorkspaceCommandRequest
    isolated_session_id: str
    run_id: str
    fencing_token: int
    started_at: float
    deadline: float
    cursor: int = 0
    sequence: int = 0
    output_bytes: int = 0
    output_truncated: bool = False
    peak_memory_bytes: int = 0
    terminal: WorkspaceCommandStatus | None = None


class _WorkspaceFiles:
    def __init__(self, compat: OpenSandboxCompat) -> None:
        self.compat = compat

    async def stat(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
    ) -> WorkspaceFileEntry:
        logical = _logical_path(path)
        async with self._session(sandbox, session_id) as isolated_id:
            info = await self.compat.isolated_stat(sandbox.sandbox_id, isolated_id, logical)
        return _isolated_file_entry(info, reject_symlink=True)

    async def list(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
    ) -> tuple[WorkspaceFileEntry, ...]:
        logical = _logical_path(path)
        async with self._session(sandbox, session_id) as isolated_id:
            values = await self.compat.isolated_list(sandbox.sandbox_id, isolated_id, logical)
        return tuple(_isolated_file_entry(value) for value in values)

    async def read(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
        *,
        offset: int = 0,
        limit: int = 1_048_576,
    ) -> WorkspaceFileChunk:
        if offset < 0 or limit <= 0 or limit > 1_048_576:
            raise WorkspaceInvalidPath("Workspace read range is invalid")
        logical = _logical_path(path)
        async with self._session(sandbox, session_id) as isolated_id:
            info = await self.compat.isolated_stat(sandbox.sandbox_id, isolated_id, logical)
            _isolated_file_entry(info, reject_symlink=True)
            content = await self.compat.isolated_read(
                sandbox.sandbox_id,
                isolated_id,
                logical,
                offset=offset,
                limit=limit,
            )
        return WorkspaceFileChunk(
            path=_logical_path(path),
            content=content,
            offset=offset,
            eof=offset + len(content) >= info.size,
            revision=_revision(info),
        )

    async def write(
        self,
        sandbox: WorkspaceSandbox,
        session_id: str,
        path: str,
        content: bytes,
        *,
        expected_revision: str | None = None,
    ) -> WorkspaceWriteResult:
        if len(content) > 10 * 1024 * 1024:
            raise WorkspaceInvalidPath("Workspace write exceeds the operation limit")
        logical = _logical_path(path)
        if logical == ".":
            raise WorkspaceInvalidPath("Workspace root cannot be overwritten")
        async with self._session(sandbox, session_id) as isolated_id:
            if expected_revision is not None:
                try:
                    current = await self.compat.isolated_stat(
                        sandbox.sandbox_id, isolated_id, logical
                    )
                except WorkspaceProviderError as exc:
                    raise WorkspaceConflict("Workspace file revision is unavailable") from exc
                _isolated_file_entry(current, reject_symlink=True)
                if _revision(current) != expected_revision:
                    raise WorkspaceConflict("Workspace file changed before write")
            parent = posixpath.dirname(logical) or "."
            await self.compat.isolated_mkdir(sandbox.sandbox_id, isolated_id, parent)
            temp = f"{logical}.pskit-{secrets.token_hex(8)}.tmp"
            await self.compat.isolated_write(sandbox.sandbox_id, isolated_id, temp, content)
            await self.compat.isolated_move(sandbox.sandbox_id, isolated_id, temp, logical)
            info = await self.compat.isolated_stat(sandbox.sandbox_id, isolated_id, logical)
        return WorkspaceWriteResult(path=logical, size=info.size, revision=_revision(info))

    @asynccontextmanager
    async def _session(self, sandbox: WorkspaceSandbox, session_id: str):
        safe_session = _identifier(session_id, "session")
        physical = f"/workspace/{safe_session}"
        await self.compat.mkdir(sandbox.sandbox_id, physical)
        session = await self.compat.create_isolated_session(
            sandbox.sandbox_id,
            physical_workspace=f"{_WORKSPACE_ALIAS_ROOT}/{safe_session}",
            uid=10001,
            gid=10001,
            idle_timeout_seconds=60,
        )
        if (
            session.workspace_path != f"{_WORKSPACE_ALIAS_ROOT}/{safe_session}"
            or session.profile != "strict"
            or session.uid != 10001
            or session.gid != 10001
            or session.share_net is not True
        ):
            await self.compat.delete_isolated_session(sandbox.sandbox_id, session.id)
            raise WorkspaceUnsafeRuntime("Workspace file Session is unsafe")
        try:
            yield session.id
        finally:
            try:
                await self.compat.delete_isolated_session(sandbox.sandbox_id, session.id)
            except WorkspaceProviderError:
                logger.warning(json.dumps({"event": "file_session_cleanup_failed"}))


class _WorkspaceCommands:
    def __init__(
        self,
        compat: OpenSandboxCompat,
        store: WorkspaceSandboxStore,
        capability_reader,
    ) -> None:
        self.compat = compat
        self.store = store
        self.capability_reader = capability_reader
        self._processes: dict[tuple[str, str], _Process] = {}

    async def start(
        self,
        sandbox: WorkspaceSandbox,
        request: WorkspaceCommandRequest,
    ) -> WorkspaceCommandHandle:
        capabilities = self.capability_reader()
        if not capabilities.command_execution or not capabilities.session_mount_namespace:
            raise WorkspaceUnsafeRuntime("Workspace command isolation is unavailable")
        if not request.argv or request.timeout_seconds <= 0 or request.max_output_bytes <= 0:
            raise WorkspaceInvalidPath("Workspace command request is invalid")
        if request.max_output_bytes > 4 * 1024 * 1024:
            raise WorkspaceInvalidPath("Workspace command output limit is too large")
        env = _safe_env(request.env)
        session_id = _identifier(request.session_id, "session")
        cwd = _logical_path(request.cwd)
        physical_session = f"/workspace/{session_id}"
        isolated_session = f"{_WORKSPACE_ALIAS_ROOT}/{session_id}"
        await self.compat.mkdir(sandbox.sandbox_id, physical_session)
        for directory in ("files", "work", "attempts", "artifacts"):
            await self.compat.mkdir(sandbox.sandbox_id, f"{physical_session}/{directory}")
        await self.compat.mkdir(sandbox.sandbox_id, f"{physical_session}/work/.tmp")
        await self.compat.mkdir(sandbox.sandbox_id, f"{physical_session}/{cwd}")
        isolated = await self.compat.create_isolated_session(
            sandbox.sandbox_id,
            physical_workspace=isolated_session,
            uid=10001,
            gid=10001,
            idle_timeout_seconds=max(30, int(request.timeout_seconds) + 15),
            readonly_paths=("files",),
        )
        if (
            isolated.workspace_path != isolated_session
            or isolated.profile != "strict"
            or isolated.uid != 10001
            or isolated.gid != 10001
            or isolated.share_net is not True
        ):
            await self.compat.delete_isolated_session(sandbox.sandbox_id, isolated.id)
            raise WorkspaceUnsafeRuntime("Workspace Session isolation was not confirmed")

        try:
            lease = self.store.acquire_lease(
                attempt_id=request.attempt_id,
                user_id=sandbox.user_id,
                session_id=session_id,
                run_id=request.run_id,
                lease_seconds=request.timeout_seconds + 30,
            )
        except Exception:
            await self.compat.delete_isolated_session(sandbox.sandbox_id, isolated.id)
            raise
        code = f"cd -- {shlex.quote('/workspace/' + cwd)} && exec {shlex.join(request.argv)}"
        try:
            run = await self.compat.run_background(sandbox.sandbox_id, isolated.id, code, env=env)
        except Exception:
            await self.compat.delete_isolated_session(sandbox.sandbox_id, isolated.id)
            self.store.release_after_exit(
                request.attempt_id,
                lease.fencing_token,
                terminal_state="failed",
                exit_code=None,
            )
            raise
        process_id = f"{isolated.id}:{run.run_id}"
        self.store.attach_process(request.attempt_id, lease.fencing_token, process_id)
        state = _Process(
            sandbox=sandbox,
            request=request,
            isolated_session_id=isolated.id,
            run_id=run.run_id,
            fencing_token=lease.fencing_token,
            started_at=time.monotonic(),
            deadline=time.monotonic() + request.timeout_seconds,
        )
        self._processes[(sandbox.sandbox_id, process_id)] = state
        return WorkspaceCommandHandle(
            sandbox_id=sandbox.sandbox_id,
            attempt_id=request.attempt_id,
            process_id=process_id,
        )

    async def _events(self, handle: WorkspaceCommandHandle):
        state = self._required(handle)
        while True:
            if state.terminal is not None:
                yield WorkspaceCommandEvent(
                    sequence=state.sequence,
                    type="exit",
                    exit_code=state.terminal.exit_code,
                )
                return
            await self._enforce_timeout(handle, state)
            logs = await self.compat.run_logs(
                handle.sandbox_id,
                state.isolated_session_id,
                state.run_id,
                cursor=state.cursor,
            )
            state.cursor = logs.cursor
            if logs.content:
                remaining = state.request.max_output_bytes - state.output_bytes
                chunk = logs.content[: max(remaining, 0)]
                if len(logs.content) > len(chunk):
                    state.output_truncated = True
                state.output_bytes += len(chunk)
                if chunk:
                    state.sequence += 1
                    yield WorkspaceCommandEvent(
                        sequence=state.sequence,
                        type="output",
                        data=chunk,
                        truncated=state.output_truncated,
                    )
            status = await self.status(handle)
            if status.state != "running":
                state.sequence += 1
                yield WorkspaceCommandEvent(
                    sequence=state.sequence,
                    type="exit",
                    exit_code=status.exit_code,
                )
                return
            await asyncio.sleep(0.15)

    def events(self, handle: WorkspaceCommandHandle):
        return self._events(handle)

    async def status(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus:
        state = self._required(handle)
        if state.terminal is not None:
            return state.terminal
        await self._enforce_timeout(handle, state)
        if state.terminal is not None:
            return state.terminal
        provider_status = await self.compat.run_status(
            handle.sandbox_id, state.isolated_session_id, state.run_id
        )
        if provider_status.running:
            await self._observe_metrics(state)
            return WorkspaceCommandStatus(state="running")
        terminal_name = (
            "completed"
            if provider_status.exit_code == 0 and not provider_status.error
            else "failed"
        )
        terminal = WorkspaceCommandStatus(
            state=terminal_name,
            exit_code=provider_status.exit_code,
            termination_confirmed=True,
        )
        await self._finish(state, terminal)
        return terminal

    async def cancel(self, handle: WorkspaceCommandHandle) -> WorkspaceCommandStatus:
        state = self._required(handle)
        if state.terminal is not None:
            return state.terminal
        # OpenSandbox 1.1.0 has no per-run isolated interrupt endpoint.  A
        # per-attempt Session is therefore deleted, which is the authoritative
        # supervisor acknowledgement that its whole process namespace ended.
        await self.compat.delete_isolated_session(handle.sandbox_id, state.isolated_session_id)
        terminal = WorkspaceCommandStatus(
            state="cancelled", exit_code=None, termination_confirmed=True
        )
        await self._finish(state, terminal, cleanup_session=False)
        return terminal

    async def metrics(self, handle: WorkspaceCommandHandle) -> WorkspaceMetrics:
        state = self._required(handle)
        observed = await self.compat.metrics(handle.sandbox_id)
        wall_ms = max(0, int((time.monotonic() - state.started_at) * 1000))
        state.peak_memory_bytes = max(state.peak_memory_bytes, observed.memory_used_bytes)
        cpu_core_ms = int(
            wall_ms * observed.cpu_count * max(0.0, observed.cpu_used_percentage) / 100
        )
        return WorkspaceMetrics(
            wall_ms=wall_ms,
            cpu_core_ms=cpu_core_ms,
            peak_memory_bytes=state.peak_memory_bytes,
        )

    async def _enforce_timeout(
        self,
        handle: WorkspaceCommandHandle,
        state: _Process,
    ) -> None:
        if state.terminal is None and time.monotonic() >= state.deadline:
            await self.cancel(handle)

    async def _observe_metrics(self, state: _Process) -> None:
        observed = await self.compat.metrics(state.sandbox.sandbox_id)
        state.peak_memory_bytes = max(state.peak_memory_bytes, observed.memory_used_bytes)

    async def _finish(
        self,
        state: _Process,
        terminal: WorkspaceCommandStatus,
        *,
        cleanup_session: bool = True,
    ) -> None:
        if state.terminal is not None:
            return
        state.terminal = terminal
        terminal_name = terminal.state
        if terminal_name not in {"completed", "failed", "cancelled"}:
            terminal_name = "failed"
        self.store.release_after_exit(
            state.request.attempt_id,
            state.fencing_token,
            terminal_state=terminal_name,
            exit_code=terminal.exit_code,
        )
        if cleanup_session:
            try:
                await self.compat.delete_isolated_session(
                    state.sandbox.sandbox_id, state.isolated_session_id
                )
            except WorkspaceProviderError:
                # The process terminal state is already authoritative; stale
                # namespace cleanup is retried by sandbox replacement/stop.
                logger.warning(json.dumps({"event": "isolated_session_cleanup_failed"}))

    def _required(self, handle: WorkspaceCommandHandle) -> _Process:
        state = self._processes.get((handle.sandbox_id, handle.process_id))
        if state is None or state.request.attempt_id != handle.attempt_id:
            raise WorkspaceConflict("Workspace command handle is stale")
        return state


class OpenSandboxWorkspaceProvider:
    """One gVisor sandbox per user with verified per-Session command mounts."""

    def __init__(
        self,
        settings: Settings,
        store: WorkspaceSandboxStore,
        *,
        compat: OpenSandboxCompat | None = None,
    ) -> None:
        self.settings = settings
        self.compat = compat or OpenSandboxCompat(
            settings.workspace_server_url,
            settings.workspace_api_key,
            connect_timeout_seconds=settings.workspace_connect_timeout_seconds,
            request_timeout_seconds=settings.workspace_request_timeout_seconds,
        )
        self.lifecycle = WorkspaceSandboxLifecycle(
            store,
            self.compat,
            image_digest=settings.workspace_image_digest,
            namespace=settings.workspace_namespace,
            cpu_millicores=settings.workspace_cpu_millicores,
            memory_bytes=settings.workspace_memory_bytes,
            disk_bytes=settings.workspace_disk_bytes,
            pid_limit=settings.workspace_pid_limit,
            inode_limit=settings.workspace_inode_limit,
            wait_timeout_seconds=settings.workspace_connect_timeout_seconds,
        )
        self._capabilities = WorkspaceCapabilities(provider="opensandbox")
        self._probe_lock = asyncio.Lock()
        self._unsafe = False
        self._last_probe_unavailable = False
        self.files = _WorkspaceFiles(self.compat)
        self.commands = _WorkspaceCommands(self.compat, store, lambda: self._capabilities)

    async def ensure_user(self, user_id: str) -> WorkspaceSandbox:
        if not self._capabilities.command_execution:
            await self.probe()
        if self._unsafe:
            raise WorkspaceUnsafeRuntime()
        return await self.lifecycle.ensure(user_id)

    async def capabilities(self) -> WorkspaceCapabilities:
        return self._capabilities

    async def readiness(self) -> WorkspaceReadiness:
        if self._unsafe:
            state = WorkspaceReadinessState.UNSAFE
            code = WorkspaceErrorCode.UNSAFE_RUNTIME.value
            detail = "Workspace isolation probe failed"
        elif not self._capabilities.command_execution:
            state = (
                WorkspaceReadinessState.UNAVAILABLE
                if self._last_probe_unavailable
                else WorkspaceReadinessState.DEGRADED
            )
            code = (
                WorkspaceErrorCode.UNAVAILABLE.value
                if self._last_probe_unavailable
                else "WORKSPACE_PROBE_REQUIRED"
            )
            detail = (
                "Workspace provider is unavailable"
                if self._last_probe_unavailable
                else "Workspace isolation has not been verified"
            )
        else:
            state = WorkspaceReadinessState.READY
            code = "WORKSPACE_READY"
            detail = ""
        return WorkspaceReadiness(
            state=state,
            code=code,
            detail=detail,
            capabilities=self._capabilities,
        )

    async def stop_user(
        self,
        user_id: str,
        expected_revision: int | None = None,
    ) -> None:
        await self.lifecycle.stop(user_id, expected_revision)

    async def replace_user(
        self,
        user_id: str,
        expected_revision: int,
    ) -> WorkspaceSandbox:
        if not self._capabilities.command_execution:
            await self.probe()
        return await self.lifecycle.replace(user_id, expected_revision)

    async def probe(self) -> WorkspaceCapabilities:
        """Prove every security capability using a disposable real sandbox."""
        async with self._probe_lock:
            if self._capabilities.command_execution and not self._unsafe:
                return self._capabilities
            spec = OpenSandboxCreateSpec(
                image_digest=self.settings.workspace_image_digest,
                volume_id=f"{self.settings.workspace_namespace}-capability-probe",
                namespace=self.settings.workspace_namespace,
                user_hash="capability-probe",
                cpu_millicores=self.settings.workspace_cpu_millicores,
                memory_bytes=self.settings.workspace_memory_bytes,
                disk_bytes=self.settings.workspace_disk_bytes,
                pid_limit=self.settings.workspace_pid_limit,
                inode_limit=self.settings.workspace_inode_limit,
            )
            first_id: str | None = None
            second_id: str | None = None
            diagnostics: list[str] = []
            try:
                first_id = (await self.compat.create(spec)).id
                await self._require_runsc(first_id)
                capabilities = await self.compat.isolation_capabilities(first_id)
                required_layers = {"cap_drop", "seccomp"}
                landlock_state = capabilities.hardening.get("landlock")
                if (
                    not capabilities.available
                    or capabilities.isolator not in {"bwrap", "bubblewrap"}
                    or not capabilities.setpriv_available
                    or any(capabilities.hardening.get(name) != "active" for name in required_layers)
                    or capabilities.hardening.get("signal_shield") != "active"
                    or landlock_state not in {"active", "degraded", "unsupported"}
                ):
                    raise WorkspaceUnsafeRuntime("Workspace hardening is incomplete")

                await self.compat.mkdir(first_id, "/workspace/probe-session")
                await self.compat.write(first_id, "/workspace/.sibling-sentinel", b"hidden")
                await self.compat.write(first_id, "/workspace/probe-session/persist", b"durable")
                session = await self.compat.create_isolated_session(
                    first_id,
                    physical_workspace=f"{_WORKSPACE_ALIAS_ROOT}/probe-session",
                    uid=10001,
                    gid=10001,
                    idle_timeout_seconds=30,
                )
                if (
                    session.workspace_path != f"{_WORKSPACE_ALIAS_ROOT}/probe-session"
                    or session.profile != "strict"
                    or session.uid != 10001
                    or session.gid != 10001
                    or session.share_net is not True
                ):
                    raise WorkspaceUnsafeRuntime("Workspace Session metadata is unsafe")
                script = """
set -eu
cd /workspace
test "$(id -u)" = 10001
test "$(id -g)" = 10001
test -f ./persist
test ! -e ./.sibling-sentinel
test ! -S /var/run/docker.sock
test ! -e /app/app
test ! -e /run/secrets
test "$(awk '/^CapEff:/{print $2}' /proc/self/status)" = 0000000000000000
test "$(awk '/^NoNewPrivs:/{print $2}' /proc/self/status)" = 1
test ! -r /proc/1/environ
test -z "${OPEN_SANDBOX_API_KEY-}${RESEARCH_AGENT_DATABASE_URL-}${SUPABASE_SECRET_KEY-}"
python - <<'PY'
import ctypes
import socket

libc = ctypes.CDLL(None, use_errno=True)
if libc.ptrace(16, 1, 0, 0) != -1:
    raise SystemExit(20)
try:
    s = socket.socket()
except OSError:
    pass
else:
    s.settimeout(1)
    try:
        s.connect(("1.1.1.1", 443))
    except OSError:
        pass
    else:
        raise SystemExit(21)
    finally:
        s.close()

for hostname in ("backend", "postgres", "supabase-db", "api-gw", "litellm"):
    try:
        socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        continue
    raise SystemExit(22)
PY
"""
                exit_code, _output = await self.compat.run_isolated(
                    first_id, session.id, script, timeout_seconds=10
                )
                if exit_code != 0:
                    raise WorkspaceUnsafeRuntime("Workspace isolation behavior is unsafe")
                await self.compat.delete_isolated_session(first_id, session.id)

                await self.compat.mkdir(first_id, "/workspace/probe-session/files")
                await self.compat.write(
                    first_id, "/workspace/probe-session/files/immutable", b"unchanged"
                )
                event_session = await self.compat.create_isolated_session(
                    first_id,
                    physical_workspace=f"{_WORKSPACE_ALIAS_ROOT}/probe-session",
                    uid=10001,
                    gid=10001,
                    idle_timeout_seconds=30,
                    readonly_paths=("files",),
                )
                run = await self.compat.run_background(
                    first_id,
                    event_session.id,
                    "test ! -w /workspace/files && printf pskit-probe",
                    env={},
                )
                event_ok = False
                for _ in range(30):
                    logs = await self.compat.run_logs(
                        first_id, event_session.id, run.run_id, cursor=0
                    )
                    status = await self.compat.run_status(first_id, event_session.id, run.run_id)
                    if (
                        b"pskit-probe" in logs.content
                        and not status.running
                        and status.exit_code == 0
                    ):
                        event_ok = True
                        break
                    await asyncio.sleep(0.1)
                if not event_ok:
                    raise WorkspaceUnsafeRuntime("Workspace command events are unavailable")
                await self.compat.delete_isolated_session(first_id, event_session.id)

                cancel_session = await self.compat.create_isolated_session(
                    first_id,
                    physical_workspace=f"{_WORKSPACE_ALIAS_ROOT}/probe-session",
                    uid=10001,
                    gid=10001,
                    idle_timeout_seconds=30,
                )
                await self.compat.run_background(first_id, cancel_session.id, "sleep 30", env={})
                await self.compat.delete_isolated_session(first_id, cancel_session.id)
                metrics = await self.compat.metrics(first_id)
                if metrics.memory_used_bytes < 0 or metrics.cpu_count <= 0:
                    raise WorkspaceUnsafeRuntime("Workspace metrics are invalid")

                await self.compat.kill(first_id)
                first_id = None
                second_id = (await self.compat.create(spec)).id
                await self._require_runsc(second_id)
                persisted = await self.compat.read(
                    second_id,
                    "/workspace/probe-session/persist",
                    offset=0,
                    limit=16,
                )
                if persisted != b"durable":
                    raise WorkspaceUnsafeRuntime("Workspace volume did not persist")
                diagnostics.extend(
                    (
                        "runsc",
                        "bwrap-strict",
                        f"landlock-{landlock_state}",
                        "internal-network",
                        "control-plane-unreachable",
                        "no-docker-socket",
                        "volume-reconnect",
                    )
                )
                self._capabilities = WorkspaceCapabilities(
                    provider="opensandbox",
                    runtime="runsc",
                    file_access=True,
                    command_execution=True,
                    command_events=True,
                    cancellation=True,
                    metrics=True,
                    persistent_volume=True,
                    session_mount_namespace=True,
                    uid=10001,
                    gid=10001,
                    diagnostics=tuple(diagnostics),
                )
                self._unsafe = False
                self._last_probe_unavailable = False
                return self._capabilities
            except WorkspaceUnsafeRuntime:
                self._unsafe = True
                self._last_probe_unavailable = False
                self._capabilities = WorkspaceCapabilities(
                    provider="opensandbox", diagnostics=("probe-failed",)
                )
                raise
            except WorkspaceProviderError:
                self._unsafe = False
                self._last_probe_unavailable = True
                self._capabilities = WorkspaceCapabilities(
                    provider="opensandbox", diagnostics=("provider-unavailable",)
                )
                raise
            except Exception as exc:
                self._unsafe = False
                self._last_probe_unavailable = True
                self._capabilities = WorkspaceCapabilities(
                    provider="opensandbox", diagnostics=("probe-failed",)
                )
                raise WorkspaceUnavailable("Workspace capability probe failed") from exc
            finally:
                for sandbox_id in (first_id, second_id):
                    if sandbox_id:
                        try:
                            await self.compat.kill(sandbox_id)
                        except WorkspaceProviderError:
                            logger.error(json.dumps({"event": "probe_cleanup_unconfirmed"}))

    async def _require_runsc(self, sandbox_id: str) -> None:
        if await self.compat.runtime(sandbox_id) != "runsc":
            raise WorkspaceUnsafeRuntime()


def _identifier(value: str, label: str) -> str:
    if (
        not value
        or len(value) > 128
        or value in {".", ".."}
        or value[0] == "."
        or any(char not in _IDENTIFIER_CHARS for char in value)
    ):
        raise WorkspaceInvalidPath(f"Workspace {label} identifier is invalid")
    return value


def _logical_path(value: str) -> str:
    if value in {"", "."}:
        return "."
    if "\x00" in value or value.startswith("/"):
        raise WorkspaceInvalidPath()
    parsed = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise WorkspaceInvalidPath()
    return parsed.as_posix()


def _revision(info: CompatFileInfo) -> str:
    value = f"{info.kind}:{info.size}:{info.modified_ns}".encode()
    return hashlib.sha256(value).hexdigest()


def _isolated_file_entry(
    info: CompatFileInfo,
    *,
    reject_symlink: bool = False,
) -> WorkspaceFileEntry:
    logical = _logical_path(info.path)
    if reject_symlink and info.kind in {"symlink", "other"}:
        raise WorkspaceInvalidPath("Workspace symbolic or special files are not allowed")
    return WorkspaceFileEntry(
        path=logical,
        kind=info.kind,
        size=info.size,
        revision=_revision(info),
    )


def _safe_env(values) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        normalized = key.upper()
        if (
            not key
            or "\x00" in key
            or "=" in key
            or any(normalized.startswith(prefix) for prefix in _RESERVED_ENV_PREFIXES)
        ):
            raise WorkspaceInvalidPath("Workspace environment contains a reserved key")
        if not isinstance(value, str) or "\x00" in value or len(value) > 16_384:
            raise WorkspaceInvalidPath("Workspace environment value is invalid")
        result[key] = value
    return result
