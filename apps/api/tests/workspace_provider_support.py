"""Filesystem test provider for the current workspace file boundary."""

import hashlib
from pathlib import Path

from app.domain.workspace_paths import WorkspacePathPolicy
from app.ports.workspace_sandbox import (
    WorkspaceCapabilities,
    WorkspaceConflict,
    WorkspaceFileChunk,
    WorkspaceFileEntry,
    WorkspaceInvalidPath,
    WorkspaceNotFound,
    WorkspaceReadiness,
    WorkspaceReadinessState,
    WorkspaceSandbox,
    WorkspaceWriteResult,
)

IMAGE_DIGEST = "test-workspace@sha256:" + "a" * 64


def workspace_settings(owner="alice"):
    return {
        "workspace_provider": "opensandbox",
        "workspace_enabled": True,
        "workspace_user_allowlist_json": f'["{owner}"]',
        "workspace_server_url": "http://127.0.0.1:8080",
        "workspace_api_key": "test-only-workspace-secret-at-least-32-bytes",
        "workspace_image_digest": IMAGE_DIGEST,
    }


class FilesystemWorkspaceProvider:
    """Keep provider file operations on one synthetic user's temporary volume."""

    def __init__(self, root: Path, owner: str = "alice", *, store=None):
        self.root = root
        self.owner = owner
        self.files = self
        self.lifecycle = self
        self.store = store

    async def ensure_user(self, user_id):
        if user_id != self.owner:
            raise WorkspaceNotFound()
        if self.store is not None:
            claim = self.store.claim_creation(
                user_id, provider="opensandbox", image_digest=IMAGE_DIGEST,
            )
            record = (
                self.store.confirm_instance(
                    user_id, claim_token=claim.record.claim_token, sandbox_id="test-sandbox",
                )
                if claim.claimed else claim.record
            )
            if record.lifecycle_state != "ready" or record.runtime_state != "running":
                raise WorkspaceConflict()
            return WorkspaceSandbox(
                user_id, record.sandbox_id, record.volume_id, record.image_digest,
                record.provider_revision, record.lifecycle_state,
            )
        return WorkspaceSandbox(user_id, "test-sandbox", "test-volume", IMAGE_DIGEST, 1, "ready")

    async def capabilities(self):
        return WorkspaceCapabilities(
            provider="opensandbox", runtime="gvisor", file_access=True,
            persistent_volume=True, session_mount_namespace=True, uid=1000, gid=1000,
        )

    async def readiness(self):
        return WorkspaceReadiness(
            WorkspaceReadinessState.READY, "WORKSPACE_READY", await self.capabilities(),
        )

    async def probe(self):
        return await self.readiness()

    async def reconcile_orphans(self):
        return 0

    async def stop_user(self, user_id, expected_revision):
        draining = self.store.begin_drain(user_id, expected_revision)
        self.store.confirm_stopped(user_id, draining.provider_revision)

    def _path(self, sandbox, session_id, path):
        if sandbox.user_id != self.owner:
            raise WorkspaceNotFound()
        resolved = WorkspacePathPolicy.logical(session_id, path)
        target = self.root / session_id / resolved.logical
        if any(item.is_symlink() for item in (target, *target.parents)):
            raise WorkspaceInvalidPath()
        return target

    async def read(self, sandbox, session_id, path, *, offset, limit):
        target = self._path(sandbox, session_id, path)
        if not target.is_file():
            raise WorkspaceNotFound()
        raw = target.read_bytes()
        return WorkspaceFileChunk(
            path, raw[offset:offset + limit], offset, offset + limit >= len(raw),
            hashlib.sha256(raw).hexdigest(),
        )

    async def write(self, sandbox, session_id, path, content, *, expected_revision=None):
        target = self._path(sandbox, session_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return WorkspaceWriteResult(path, len(content), hashlib.sha256(content).hexdigest())

    async def list(self, sandbox, session_id, path):
        target = self._path(sandbox, session_id, path)
        if not target.is_dir():
            raise WorkspaceNotFound()
        return tuple(
            WorkspaceFileEntry(
                f"{path}/{child.name}",
                "symlink" if child.is_symlink() else "directory" if child.is_dir() else "file",
                child.stat(follow_symlinks=False).st_size,
            )
            for child in sorted(target.iterdir())
        )
