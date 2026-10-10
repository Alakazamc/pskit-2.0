"""Durable lifecycle coordination for user-owned OpenSandbox instances."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from app.adapters.live.opensandbox_compat import (
    OpenSandboxCompat,
    OpenSandboxCreateSpec,
)
from app.domain.workspace_sandboxes import (
    WorkspaceCreationClaim,
    WorkspaceSandboxRecord,
    WorkspaceSandboxStore,
)
from app.ports.workspace_sandbox import (
    WorkspaceConflict,
    WorkspaceErrorCode,
    WorkspaceProviderError,
    WorkspaceSandbox,
    WorkspaceTimeout,
    WorkspaceUnavailable,
    WorkspaceUnsafeRuntime,
)

logger = logging.getLogger("pskit.workspace.lifecycle")


class WorkspaceSandboxLifecycle:
    """Serialize create/stop/replace and clean up unowned provider instances."""

    def __init__(
        self,
        store: WorkspaceSandboxStore,
        compat: OpenSandboxCompat,
        *,
        image_digest: str,
        namespace: str,
        cpu_millicores: int,
        memory_bytes: int,
        disk_bytes: int,
        pid_limit: int,
        inode_limit: int,
        wait_timeout_seconds: float,
    ) -> None:
        self.store = store
        self.compat = compat
        self.image_digest = image_digest
        self.namespace = namespace
        self.cpu_millicores = cpu_millicores
        self.memory_bytes = memory_bytes
        self.disk_bytes = disk_bytes
        self.pid_limit = pid_limit
        self.inode_limit = inode_limit
        self.wait_timeout_seconds = wait_timeout_seconds

    async def ensure(self, user_id: str) -> WorkspaceSandbox:
        claim = self.store.claim_creation(
            user_id,
            provider="opensandbox",
            image_digest=self.image_digest,
        )
        if claim.claimed:
            return await self._create_claimed(claim)

        record = claim.record
        if record.lifecycle_state in {"creating", "replacing"}:
            record = await self._wait_for_claim(user_id, record.provider_revision)
        if record.lifecycle_state == "ready" and record.runtime_state == "running":
            return await self._connect_verified(record)
        if record.lifecycle_state in {"ready", "error"}:
            replacement = self.store.begin_replace(user_id, record.provider_revision)
            return await self._create_claimed(
                WorkspaceCreationClaim(replacement, True), old_sandbox_id=record.sandbox_id
            )
        raise WorkspaceUnavailable()

    async def replace(self, user_id: str, expected_revision: int) -> WorkspaceSandbox:
        current = self.store.get(user_id)
        old_sandbox_id = current.sandbox_id if current else None
        record = self.store.begin_replace(user_id, expected_revision)
        return await self._create_claimed(
            WorkspaceCreationClaim(record, True), old_sandbox_id=old_sandbox_id
        )

    async def stop(
        self,
        user_id: str,
        expected_revision: int | None = None,
    ) -> WorkspaceSandboxRecord | None:
        record = self.store.get(user_id)
        if record is None or record.sandbox_id is None:
            return None
        revision = record.provider_revision if expected_revision is None else expected_revision
        draining = self.store.begin_drain(user_id, revision)
        if draining.runtime_state == "stopped":
            return draining
        try:
            await self.compat.kill(record.sandbox_id)
        except WorkspaceProviderError as exc:
            try:
                self.store.fail_drain(
                    user_id,
                    draining.provider_revision,
                    exc.code.value,
                )
            except WorkspaceConflict:
                pass
            self._audit("sandbox_stop_failed", user_id, exc.code.value)
            raise
        stopped = self.store.confirm_stopped(user_id, draining.provider_revision)
        self._audit("sandbox_stopped", user_id, "WORKSPACE_STOPPED")
        return stopped

    async def reconcile_orphans(self) -> int:
        """Kill namespace instances that are not the current durable owner."""
        records = self.store.list()
        owned = {
            record.sandbox_id
            for record in records
            if record.sandbox_id and record.lifecycle_state in {"ready", "replacing"}
        }
        claimed_owner_hashes = {
            self._owner_hash(record.user_id)
            for record in records
            if record.claim_token
            and record.claim_expires_at is not None
            and record.claim_expires_at > time.time()
            and record.lifecycle_state in {"creating", "replacing"}
        }
        killed = 0
        for sandbox_id, metadata in await self.compat.list_owned(self.namespace):
            if sandbox_id in owned or metadata.get("pskit.owner_hash") in claimed_owner_hashes:
                continue
            await self.compat.kill(sandbox_id)
            killed += 1
        if killed:
            logger.warning(json.dumps({"event": "orphan_cleanup", "count": killed}))
        return killed

    async def _create_claimed(
        self,
        claim: WorkspaceCreationClaim,
        *,
        old_sandbox_id: str | None = None,
    ) -> WorkspaceSandbox:
        record = claim.record
        if not claim.claimed or not record.claim_token:
            raise WorkspaceConflict("Workspace creation is not owned by this request")
        owner_hash = self._owner_hash(record.user_id)
        created_id: str | None = None
        try:
            created = await self.compat.create(
                OpenSandboxCreateSpec(
                    image_digest=self.image_digest,
                    volume_id=record.volume_id,
                    namespace=self.namespace,
                    user_hash=owner_hash,
                    cpu_millicores=self.cpu_millicores,
                    memory_bytes=self.memory_bytes,
                    disk_bytes=self.disk_bytes,
                    pid_limit=self.pid_limit,
                    inode_limit=self.inode_limit,
                )
            )
            created_id = created.id
            await self._require_runsc(created_id)
            confirmed = self.store.confirm_instance(
                record.user_id,
                claim_token=record.claim_token,
                sandbox_id=created_id,
                runtime_state="running",
            )
        except WorkspaceProviderError as exc:
            if created_id:
                await self._best_effort_kill(created_id)
            self._best_effort_mark_error(record, exc.code.value)
            self._audit("sandbox_create_failed", record.user_id, exc.code.value)
            raise
        except Exception as exc:
            if created_id:
                await self._best_effort_kill(created_id)
            self._best_effort_mark_error(record, WorkspaceErrorCode.UNAVAILABLE.value)
            self._audit(
                "sandbox_create_failed", record.user_id, WorkspaceErrorCode.UNAVAILABLE.value
            )
            raise WorkspaceUnavailable() from exc

        if old_sandbox_id and old_sandbox_id != created_id:
            await self._best_effort_kill(old_sandbox_id)
        self._audit("sandbox_ready", record.user_id, "WORKSPACE_READY")
        return self._to_sandbox(confirmed)

    async def _connect_verified(self, record: WorkspaceSandboxRecord) -> WorkspaceSandbox:
        if record.sandbox_id is None:
            raise WorkspaceUnavailable()
        await self.compat.connect(record.sandbox_id)
        await self._require_runsc(record.sandbox_id)
        self.store.mark_runtime(record.user_id, "running")
        refreshed = self.store.get(record.user_id)
        if refreshed is None:
            raise WorkspaceUnavailable()
        return self._to_sandbox(refreshed)

    async def _require_runsc(self, sandbox_id: str) -> None:
        runtime = await self.compat.runtime(sandbox_id)
        if runtime != "runsc":
            raise WorkspaceUnsafeRuntime()

    async def _wait_for_claim(
        self,
        user_id: str,
        initial_revision: int,
    ) -> WorkspaceSandboxRecord:
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            record = self.store.get(user_id)
            if record is None:
                raise WorkspaceUnavailable()
            if record.lifecycle_state not in {"creating", "replacing"}:
                return record
            if record.provider_revision != initial_revision and record.claim_expires_at:
                initial_revision = record.provider_revision
        raise WorkspaceTimeout("Workspace creation did not complete")

    async def _best_effort_kill(self, sandbox_id: str) -> None:
        try:
            await self.compat.kill(sandbox_id)
        except WorkspaceProviderError:
            logger.error(
                json.dumps({"event": "sandbox_cleanup_unconfirmed", "code": "KILL_FAILED"})
            )

    def _best_effort_mark_error(
        self,
        record: WorkspaceSandboxRecord,
        error_code: str,
    ) -> None:
        if not record.claim_token:
            return
        try:
            self.store.mark_error(record.user_id, record.claim_token, error_code)
        except WorkspaceConflict:
            pass

    def _audit(self, event: str, user_id: str, code: str) -> None:
        logger.info(
            json.dumps(
                {"event": event, "owner_hash": self._owner_hash(user_id), "code": code},
                sort_keys=True,
            )
        )

    @staticmethod
    def _owner_hash(user_id: str) -> str:
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _to_sandbox(record: WorkspaceSandboxRecord) -> WorkspaceSandbox:
        if record.sandbox_id is None:
            raise WorkspaceUnavailable()
        return WorkspaceSandbox(
            user_id=record.user_id,
            sandbox_id=record.sandbox_id,
            volume_id=record.volume_id,
            image_digest=record.image_digest,
            provider_revision=record.provider_revision,
            lifecycle_state=record.lifecycle_state,
        )
