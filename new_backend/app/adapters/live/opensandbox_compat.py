"""The only module allowed to import the pinned OpenSandbox Python SDK.

OpenSandbox is deliberately kept behind small, project-owned data objects.  A
minor upstream shape change therefore cannot leak into the workspace tools or
the Agent service.  The imports are lazy so an installation configured with
the disabled provider can still start without the optional runtime package.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ports.workspace_sandbox import (
    WorkspaceCapacityExceeded,
    WorkspaceNotFound,
    WorkspaceProviderError,
    WorkspaceTimeout,
    WorkspaceUnavailable,
)

SDK_VERSION = "1.1.0"
API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"
_RUNTIME_LINE = re.compile(r"^(?:OCI\s+)?Runtime:\s*([^\s]+)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OpenSandboxCreateSpec:
    """Server-controlled inputs for one user sandbox."""

    image_digest: str
    volume_id: str
    namespace: str
    user_hash: str
    cpu_millicores: int
    memory_bytes: int
    disk_bytes: int
    pid_limit: int
    inode_limit: int


@dataclass(frozen=True, slots=True)
class CompatSandbox:
    id: str


@dataclass(frozen=True, slots=True)
class CompatFileInfo:
    path: str
    kind: str
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class CompatIsolationCapabilities:
    available: bool
    isolator: str | None
    setpriv_available: bool
    userns_available: bool
    hardening: dict[str, str]


@dataclass(frozen=True, slots=True)
class CompatIsolatedSession:
    id: str
    workspace_path: str | None
    profile: str | None
    uid: int | None
    gid: int | None
    share_net: bool | None


@dataclass(frozen=True, slots=True)
class CompatBackgroundRun:
    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class CompatRunStatus:
    running: bool
    exit_code: int | None
    error: bool


@dataclass(frozen=True, slots=True)
class CompatRunLogs:
    content: bytes
    cursor: int


@dataclass(frozen=True, slots=True)
class CompatMetrics:
    cpu_count: float
    cpu_used_percentage: float
    memory_used_bytes: int
    timestamp_ms: int


class OpenSandboxCompat:
    """Small fail-closed wrapper over OpenSandbox SDK 1.1.0."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        *,
        connect_timeout_seconds: float,
        request_timeout_seconds: float,
    ) -> None:
        parsed = urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenSandbox server URL must be HTTP(S)")
        self._base_url = server_url.rstrip("/")
        self._domain = parsed.netloc
        self._protocol = parsed.scheme
        self._api_key = api_key
        self._connect_timeout = connect_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._handles: dict[str, Any] = {}
        self._sessions: dict[tuple[str, str], Any] = {}
        self._sdk_cache: SimpleNamespace | None = None

    def _sdk(self) -> SimpleNamespace:
        if self._sdk_cache is not None:
            return self._sdk_cache
        try:
            import opensandbox
            from opensandbox import Sandbox, SandboxManager
            from opensandbox.config import ConnectionConfig
            from opensandbox.models.isolated import (
                BindMount,
                CreateIsolatedSessionRequest,
                EnvPassthroughSpec,
                IsolatedRunOpts,
                IsolatedWorkspaceSpec,
            )
            from opensandbox.models.sandboxes import PVC, SandboxFilter, Volume
        except ImportError as exc:
            raise WorkspaceUnavailable("OpenSandbox SDK is unavailable") from exc
        version = getattr(opensandbox, "__version__", SDK_VERSION)
        if version != SDK_VERSION:
            raise WorkspaceUnavailable("OpenSandbox SDK version is not approved")
        self._sdk_cache = SimpleNamespace(
            Sandbox=Sandbox,
            SandboxManager=SandboxManager,
            ConnectionConfig=ConnectionConfig,
            BindMount=BindMount,
            CreateIsolatedSessionRequest=CreateIsolatedSessionRequest,
            EnvPassthroughSpec=EnvPassthroughSpec,
            IsolatedRunOpts=IsolatedRunOpts,
            IsolatedWorkspaceSpec=IsolatedWorkspaceSpec,
            PVC=PVC,
            SandboxFilter=SandboxFilter,
            Volume=Volume,
        )
        return self._sdk_cache

    def _connection(self):
        sdk = self._sdk()
        return sdk.ConnectionConfig(
            api_key=self._api_key,
            domain=self._domain,
            protocol=self._protocol,
            request_timeout=timedelta(seconds=self._request_timeout),
            use_server_proxy=True,
            debug=False,
        )

    async def create(self, spec: OpenSandboxCreateSpec) -> CompatSandbox:
        """Create from fixed image, named volume, limits and metadata only."""
        sdk = self._sdk()
        volume = sdk.Volume(
            name="workspace",
            pvc=sdk.PVC(
                claim_name=spec.volume_id,
                create_if_not_exists=True,
                delete_on_sandbox_termination=False,
                storage=f"{max(1, math.ceil(spec.disk_bytes / (1024**3)))}Gi",
            ),
            mount_path="/workspace",
            read_only=False,
        )
        metadata = {
            "pskit.namespace": spec.namespace,
            "pskit.owner_hash": spec.user_hash,
            "pskit.volume": spec.volume_id,
            "pskit.sdk": SDK_VERSION,
        }
        extensions = {
            "bootstrap.execd.isolation": "enable",
            "pskit.network_boundary": "docker-internal",
            "pskit.pid_limit": str(spec.pid_limit),
            "pskit.inode_limit": str(spec.inode_limit),
        }
        try:
            handle = await sdk.Sandbox.create(
                spec.image_digest,
                timeout=None,
                ready_timeout=timedelta(seconds=self._connect_timeout),
                env={
                    "PSKIT_WORKSPACE_ROOT": "/workspace",
                    "PSKIT_GVISOR_BWRAP_COMPAT": "1",
                    "EXECD_ISOLATION_CONFIG": "/opt/pskit-sandbox/isolation.toml",
                    "PATH": "/opt/opensandbox:/usr/local/bin:/usr/bin:/bin",
                },
                metadata=metadata,
                resource={
                    "cpu": f"{spec.cpu_millicores / 1000:g}",
                    "memory": f"{max(1, math.ceil(spec.memory_bytes / (1024**2)))}Mi",
                },
                # OpenSandbox 1.1.0's egress sidecar is incompatible with
                # gVisor.  A5 provides a Docker internal network instead.
                network_policy=None,
                extensions=extensions,
                entrypoint=["/opt/pskit-sandbox/entrypoint.sh"],
                volumes=[volume],
                connection_config=self._connection(),
            )
        except Exception as exc:
            raise self._translate(exc, "create") from exc
        self._handles[handle.id] = handle
        return CompatSandbox(id=handle.id)

    async def connect(self, sandbox_id: str) -> CompatSandbox:
        await self._handle(sandbox_id)
        return CompatSandbox(id=sandbox_id)

    async def _handle(self, sandbox_id: str):
        existing = self._handles.get(sandbox_id)
        if existing is not None:
            return existing
        sdk = self._sdk()
        try:
            handle = await sdk.Sandbox.connect(
                sandbox_id,
                connection_config=self._connection(),
                connect_timeout=timedelta(seconds=self._connect_timeout),
            )
        except Exception as exc:
            raise self._translate(exc, "connect") from exc
        self._handles[sandbox_id] = handle
        return handle

    async def kill(self, sandbox_id: str) -> None:
        handle = self._handles.pop(sandbox_id, None)
        try:
            if handle is not None:
                await handle.kill()
                await handle.close()
            else:
                manager = await self._sdk().SandboxManager.create(self._connection())
                try:
                    await manager.kill_sandbox(sandbox_id)
                finally:
                    await manager.close()
        except Exception as exc:
            raise self._translate(exc, "kill") from exc
        for key in [key for key in self._sessions if key[0] == sandbox_id]:
            self._sessions.pop(key, None)

    async def list_owned(self, namespace: str) -> tuple[tuple[str, dict[str, str]], ...]:
        manager = None
        try:
            sdk = self._sdk()
            manager = await sdk.SandboxManager.create(self._connection())
            page = await manager.list_sandbox_infos(
                sdk.SandboxFilter(metadata={"pskit.namespace": namespace}, page_size=100)
            )
            return tuple((item.id, dict(item.metadata or {})) for item in page.sandbox_infos)
        except Exception as exc:
            raise self._translate(exc, "list") from exc
        finally:
            if manager is not None:
                await manager.close()

    async def runtime(self, sandbox_id: str) -> str:
        """Read only the sanitized runtime field from the lifecycle diagnostic."""
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={API_KEY_HEADER: self._api_key, "Accept": "text/plain"},
                timeout=self._request_timeout,
            ) as client:
                response = await client.get(f"/sandboxes/{sandbox_id}/diagnostics/inspect")
                response.raise_for_status()
        except Exception as exc:
            raise self._translate(exc, "inspect") from exc
        for line in response.text.splitlines():
            match = _RUNTIME_LINE.match(line.strip())
            if match:
                return match.group(1).lower()
        return ""

    async def stat(self, sandbox_id: str, path: str) -> CompatFileInfo:
        handle = await self._handle(sandbox_id)
        try:
            result = await handle.files.get_file_info([path])
            info = result[path]
            modified_ns = int(info.modified_at.timestamp() * 1_000_000_000)
            return CompatFileInfo(
                path=info.path,
                kind=info.entry_type or "unknown",
                size=info.size,
                modified_ns=modified_ns,
            )
        except Exception as exc:
            raise self._translate(exc, "stat") from exc

    async def list(self, sandbox_id: str, path: str) -> tuple[CompatFileInfo, ...]:
        handle = await self._handle(sandbox_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "DirectoryListEntry")(
                path=path, depth=1
            )
            values = await handle.files.list_directory(entry)
            return tuple(
                CompatFileInfo(
                    path=value.path,
                    kind=value.entry_type or "unknown",
                    size=value.size,
                    modified_ns=int(value.modified_at.timestamp() * 1_000_000_000),
                )
                for value in values
            )
        except Exception as exc:
            raise self._translate(exc, "list_files") from exc

    async def read(self, sandbox_id: str, path: str, *, offset: int, limit: int) -> bytes:
        handle = await self._handle(sandbox_id)
        range_header = f"bytes={offset}-{offset + limit - 1}"
        try:
            return await handle.files.read_bytes(path, range_header=range_header)
        except Exception as exc:
            raise self._translate(exc, "read") from exc

    async def write(self, sandbox_id: str, path: str, content: bytes) -> None:
        handle = await self._handle(sandbox_id)
        try:
            await handle.files.write_file(path, content, mode=600, owner="pskit", group="pskit")
        except Exception as exc:
            raise self._translate(exc, "write") from exc

    async def mkdir(self, sandbox_id: str, path: str) -> None:
        handle = await self._handle(sandbox_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "WriteEntry")(
                path=path, mode=700, owner="pskit", group="pskit"
            )
            await handle.files.create_directories([entry])
        except Exception as exc:
            raise self._translate(exc, "mkdir") from exc

    async def move(self, sandbox_id: str, source: str, destination: str) -> None:
        handle = await self._handle(sandbox_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "MoveEntry")(
                source=source, destination=destination
            )
            await handle.files.move_files([entry])
        except Exception as exc:
            raise self._translate(exc, "move") from exc

    async def isolation_capabilities(self, sandbox_id: str) -> CompatIsolationCapabilities:
        handle = await self._handle(sandbox_id)
        try:
            value = await handle.isolation.capabilities()
        except Exception as exc:
            raise self._translate(exc, "isolation_capabilities") from exc
        hardening: dict[str, str] = {}
        if value.hardening is not None:
            for name in ("cap_drop", "seccomp", "landlock", "ebpf"):
                layer = getattr(value.hardening, name, None)
                hardening[name] = getattr(layer, "state", "") or ""
            hardening["signal_shield"] = "active" if value.hardening.signal_shield else "disabled"
        return CompatIsolationCapabilities(
            available=value.available,
            isolator=value.isolator,
            setpriv_available=value.setpriv_available,
            userns_available=value.userns_available,
            hardening=hardening,
        )

    async def create_isolated_session(
        self,
        sandbox_id: str,
        *,
        physical_workspace: str,
        uid: int,
        gid: int,
        idle_timeout_seconds: int,
        readonly_paths: tuple[str, ...] = (),
    ) -> CompatIsolatedSession:
        handle = await self._handle(sandbox_id)
        sdk = self._sdk()
        request = sdk.CreateIsolatedSessionRequest(
            workspace=sdk.IsolatedWorkspaceSpec(path=physical_workspace, mode="rw"),
            profile="strict",
            binds=[
                sdk.BindMount(source=physical_workspace, dest="/workspace", readonly=False),
                *[
                    sdk.BindMount(
                        source=f"{physical_workspace}/{path}",
                        dest=f"/workspace/{path}",
                        readonly=True,
                    )
                    for path in readonly_paths
                ],
            ],
            share_net=False,
            env_passthrough=sdk.EnvPassthroughSpec(mode="allow", keys=[]),
            uid=uid,
            gid=gid,
            uid_mode="setpriv",
            idle_timeout_seconds=idle_timeout_seconds,
        )
        try:
            session = await handle.isolation.create(request)
        except Exception as exc:
            raise self._translate(exc, "create_isolated_session") from exc
        self._sessions[(sandbox_id, session.session_id)] = session
        info = session.info
        return CompatIsolatedSession(
            id=session.session_id,
            workspace_path=getattr(getattr(info, "workspace", None), "path", None),
            profile=getattr(info, "profile", None),
            uid=getattr(info, "uid", None),
            gid=getattr(info, "gid", None),
            share_net=getattr(info, "share_net", None),
        )

    async def run_isolated(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 15,
    ) -> tuple[int | None, bytes]:
        session = await self._session(sandbox_id, session_id)
        try:
            result = await session.run(
                code,
                opts=self._sdk().IsolatedRunOpts(envs=env or None, timeout_seconds=timeout_seconds),
            )
        except Exception as exc:
            raise self._translate(exc, "run_isolated") from exc
        return result.exit_code, result.text.encode("utf-8", errors="replace")

    async def isolated_stat(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
    ) -> CompatFileInfo:
        session = await self._session(sandbox_id, session_id)
        try:
            result = await session.files.get_file_info([path])
            info = result[path]
            return CompatFileInfo(
                path=info.path,
                kind=info.entry_type or "unknown",
                size=info.size,
                modified_ns=int(info.modified_at.timestamp() * 1_000_000_000),
            )
        except Exception as exc:
            raise self._translate(exc, "isolated_stat") from exc

    async def isolated_list(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
    ) -> tuple[CompatFileInfo, ...]:
        session = await self._session(sandbox_id, session_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "DirectoryListEntry")(
                path=path, depth=1
            )
            values = await session.files.list_directory(entry)
            return tuple(
                CompatFileInfo(
                    path=value.path,
                    kind=value.entry_type or "unknown",
                    size=value.size,
                    modified_ns=int(value.modified_at.timestamp() * 1_000_000_000),
                )
                for value in values
            )
        except Exception as exc:
            raise self._translate(exc, "isolated_list") from exc

    async def isolated_read(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
        *,
        offset: int,
        limit: int,
    ) -> bytes:
        session = await self._session(sandbox_id, session_id)
        try:
            return await session.files.read_bytes(
                path, range_header=f"bytes={offset}-{offset + limit - 1}"
            )
        except Exception as exc:
            raise self._translate(exc, "isolated_read") from exc

    async def isolated_write(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
        content: bytes,
    ) -> None:
        session = await self._session(sandbox_id, session_id)
        try:
            await session.files.write_file(path, content, mode=600)
        except Exception as exc:
            raise self._translate(exc, "isolated_write") from exc

    async def isolated_mkdir(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
    ) -> None:
        session = await self._session(sandbox_id, session_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "WriteEntry")(
                path=path, mode=700
            )
            await session.files.create_directories([entry])
        except Exception as exc:
            raise self._translate(exc, "isolated_mkdir") from exc

    async def isolated_move(
        self,
        sandbox_id: str,
        session_id: str,
        source: str,
        destination: str,
    ) -> None:
        session = await self._session(sandbox_id, session_id)
        try:
            entry = self._sdk_import("opensandbox.models.filesystem", "MoveEntry")(
                source=source, destination=destination
            )
            await session.files.move_files([entry])
        except Exception as exc:
            raise self._translate(exc, "isolated_move") from exc

    async def run_background(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        *,
        env: dict[str, str],
    ) -> CompatBackgroundRun:
        session = await self._session(sandbox_id, session_id)
        try:
            run = await session.run_background(
                code, opts=self._sdk().IsolatedRunOpts(envs=env or None)
            )
        except Exception as exc:
            raise self._translate(exc, "run_background") from exc
        return CompatBackgroundRun(session_id=run.session_id, run_id=run.run_id)

    async def run_status(
        self,
        sandbox_id: str,
        session_id: str,
        run_id: str,
    ) -> CompatRunStatus:
        session = await self._session(sandbox_id, session_id)
        try:
            status = await session.run_status(run_id)
        except Exception as exc:
            raise self._translate(exc, "run_status") from exc
        return CompatRunStatus(
            running=status.running,
            exit_code=status.exit_code,
            error=bool(status.error),
        )

    async def run_logs(
        self,
        sandbox_id: str,
        session_id: str,
        run_id: str,
        *,
        cursor: int,
    ) -> CompatRunLogs:
        session = await self._session(sandbox_id, session_id)
        try:
            logs = await session.run_logs(run_id, cursor=cursor)
        except Exception as exc:
            raise self._translate(exc, "run_logs") from exc
        return CompatRunLogs(
            content=logs.text.encode("utf-8", errors="replace"), cursor=logs.cursor
        )

    async def delete_isolated_session(self, sandbox_id: str, session_id: str) -> None:
        session = await self._session(sandbox_id, session_id)
        try:
            await session.delete()
        except Exception as exc:
            raise self._translate(exc, "delete_isolated_session") from exc
        finally:
            self._sessions.pop((sandbox_id, session_id), None)

    async def _session(self, sandbox_id: str, session_id: str):
        existing = self._sessions.get((sandbox_id, session_id))
        if existing is not None:
            return existing
        handle = await self._handle(sandbox_id)
        try:
            session = await handle.isolation.attach(session_id)
        except Exception as exc:
            raise self._translate(exc, "attach_isolated_session") from exc
        self._sessions[(sandbox_id, session_id)] = session
        return session

    async def metrics(self, sandbox_id: str) -> CompatMetrics:
        handle = await self._handle(sandbox_id)
        try:
            value = await handle.get_metrics()
        except Exception as exc:
            raise self._translate(exc, "metrics") from exc
        return CompatMetrics(
            cpu_count=value.cpu_count,
            cpu_used_percentage=value.cpu_used_percentage,
            memory_used_bytes=int(value.memory_used_in_mib * 1024 * 1024),
            timestamp_ms=value.timestamp,
        )

    @staticmethod
    def _sdk_import(module: str, name: str):
        import importlib

        return getattr(importlib.import_module(module), name)

    @staticmethod
    def _translate(exc: Exception, operation: str) -> WorkspaceProviderError:
        name = type(exc).__name__.lower()
        status = getattr(exc, "status_code", None)
        if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)) or "timeout" in name:
            return WorkspaceTimeout(f"Workspace {operation} timed out")
        if status == 429 or "ratelimit" in name or "poolempty" in name:
            return WorkspaceCapacityExceeded()
        if status == 404 or "notfound" in name or "filenotfound" in name:
            return WorkspaceNotFound()
        return WorkspaceUnavailable(f"Workspace {operation} is unavailable")
