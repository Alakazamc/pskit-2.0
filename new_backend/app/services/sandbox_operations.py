"""Provider-neutral workspace administration and lease reconciliation."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx

from app.contracts.sandbox import SandboxMetrics, SandboxOperation, SandboxSummary
from app.domain.sandboxes import SandboxConflict as LegacySandboxConflict
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.ports.workspace_sandbox import WorkspaceSandboxProvider

logger = logging.getLogger("pskit.workspace.reconciler")


class SandboxOperations:
    """Expose stable admin operations without leaking provider SDK objects."""

    def __init__(
        self,
        provider: WorkspaceSandboxProvider,
        store: WorkspaceSandboxStore,
    ) -> None:
        self.provider = provider
        self.store = store

    async def list(self) -> list[SandboxSummary]:
        rows: list[SandboxSummary] = []
        for record in self.store.list():
            leases = self.store.leases_for_user(record.user_id)
            active_sessions = sorted(
                {lease.session_id for lease in leases if lease.state != "released"}
            )
            completed = [
                lease.updated_at
                for lease in leases
                if lease.state == "released" and lease.exit_confirmed
            ]
            rows.append(
                SandboxSummary(
                    owner_id=record.user_id,
                    instance_id=record.sandbox_id or "pending",
                    volume_id=record.volume_id,
                    image_digest=record.image_digest,
                    state=(
                        "replacing"
                        if record.lifecycle_state == "creating"
                        else record.lifecycle_state
                    ),
                    runtime_state=(
                        "unknown" if record.runtime_state == "pending" else record.runtime_state
                    ),
                    active_sessions=active_sessions,
                    revision=record.provider_revision,
                    last_completed_at=max(completed, default=record.last_confirmed_at),
                )
            )
        return rows

    async def stop(self, owner_id: str, expected_revision: int) -> SandboxOperation:
        await self.provider.stop_user(owner_id, expected_revision)
        current = self.store.get(owner_id)
        if current is None:
            raise LookupError("Workspace owner is unavailable")
        return SandboxOperation(
            operation_id="operation-" + uuid.uuid4().hex,
            owner_id=owner_id,
            kind="drain",
            state="completed",
            revision=current.provider_revision,
        )

    async def drain(self, owner_id: str, expected_revision: int) -> SandboxOperation:
        """Fence new attempts and stop the current instance, retaining its volume."""
        return await self.stop(owner_id, expected_revision)

    async def replace_keep_volume(
        self,
        owner_id: str,
        expected_revision: int,
    ) -> SandboxOperation:
        sandbox = await self.provider.replace_user(owner_id, expected_revision)
        return SandboxOperation(
            operation_id="operation-" + uuid.uuid4().hex,
            owner_id=owner_id,
            kind="replace",
            state="completed",
            revision=sandbox.provider_revision,
            image_digest=sandbox.image_digest,
        )

    async def read_usage(self, owner_id: str) -> SandboxMetrics:
        """Return an explicit unknown sample when no attempt-scoped metric exists."""
        if self.store.get(owner_id) is None:
            raise LookupError("Workspace owner is unavailable")
        return SandboxMetrics(
            owner_id=owner_id,
            cpu_core_ms=None,
            memory_bytes=None,
            storage_bytes=None,
            sampled_at=time.time(),
        )


class WorkspaceLeaseReconciler:
    """Fence expired leases and remove provider instances with no durable owner."""

    def __init__(
        self,
        store: WorkspaceSandboxStore,
        reconcile_orphans: Callable[[], Awaitable[int]],
        *,
        interval_seconds: float = 15.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Workspace reconcile interval must be positive")
        self.store = store
        self.reconcile_orphans = reconcile_orphans
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop reconciliation without touching instances or persistent volumes."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def reconcile_once(self) -> None:
        self.store.mark_expired_unknown()
        await self.reconcile_orphans()

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retry after a sanitized control-plane event
                logger.warning("workspace_reconcile_failed")
            await asyncio.sleep(self.interval_seconds)


class LegacySandboxOperations:
    """Compatibility client for the retired private Docker manager overlay."""

    def __init__(self, manager_url: str, manager_token: str, *, transport=None):
        self.manager_url = manager_url.rstrip("/")
        self.manager_token = manager_token
        self.transport = transport

    async def _request(self, method, path, **kwargs):
        async with httpx.AsyncClient(
            base_url=self.manager_url, transport=self.transport, timeout=30, trust_env=False
        ) as client:
            response = await client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self.manager_token}"},
                **kwargs,
            )
            if response.status_code == 409:
                raise LegacySandboxConflict(response.json().get("detail", "Sandbox conflict"))
            response.raise_for_status()
            return response.json()

    async def list(self) -> list[SandboxSummary]:
        return [
            SandboxSummary.model_validate(item)
            for item in await self._request("GET", "/v1/sandboxes")
        ]

    async def drain(self, owner_id: str, expected_revision: int) -> SandboxOperation:
        return SandboxOperation.model_validate(
            await self._request(
                "POST",
                f"/v1/sandboxes/{owner_id}/drain",
                json={"expected_revision": expected_revision},
            )
        )

    async def replace_keep_volume(
        self,
        owner_id: str,
        image_digest: str,
    ) -> SandboxOperation:
        return SandboxOperation.model_validate(
            await self._request(
                "POST",
                f"/v1/sandboxes/{owner_id}/replace",
                json={"image_digest": image_digest},
            )
        )

    async def read_usage(self, owner_id: str) -> SandboxMetrics:
        return SandboxMetrics.model_validate(
            await self._request("GET", f"/v1/sandboxes/{owner_id}/usage")
        )
