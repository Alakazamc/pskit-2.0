"""Bounded, ownership checked file transfer without following workspace symlinks."""

import base64
import hashlib
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from app.contracts.sandbox import WorkspaceFileRef
from app.domain.catalog import ContextNotFound
from app.domain.workspace_paths import WorkspacePathPolicy
from app.ports.workspace_sandbox import WorkspaceNotFound
from app.services.workspace_files import WorkspaceFiles, WorkspaceOperationContext

MAX_WORKSPACE_FILE_BYTES = 20 * 1024 * 1024
MAX_WORKSPACE_EXPORT_BYTES = 100 * 1024 * 1024


class LocalWorkspace:
    """Access only scoped session directories through O_NOFOLLOW directory handles."""

    def __init__(self, root: Path, max_bytes=MAX_WORKSPACE_FILE_BYTES):
        self.root = root
        self.max_bytes = max_bytes

    @staticmethod
    def _identity(value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Invalid workspace identity")
        return value

    @staticmethod
    def _parts(value):
        parts = PurePosixPath(value).parts
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Invalid workspace path")
        if any(not part or len(part) > 255 or "\x00" in part for part in parts):
            raise ValueError("Invalid workspace path")
        return parts

    @contextmanager
    def directory(self, parts, *, create=False):
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = None
        try:
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        except OSError as exc:
            raise ValueError("Workspace path is unavailable or contains a symlink") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read(self, descriptor, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_bytes:
                raise ValueError("Workspace file exceeds limit or is not regular")
            with os.fdopen(handle, "rb", closefd=False) as stream:
                raw = stream.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("Workspace file exceeds limit")
            return raw
        finally:
            os.close(handle)

    def put(self, session_id, relative_path, content, digest):
        parts = self._parts(relative_path)
        self._identity(session_id)
        if len(parts) != 2 or parts[0] != "files":
            raise ValueError("Uploads belong to the session files directory")
        if len(content) > self.max_bytes or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Workspace size or digest mismatch")
        with self.directory([session_id, "files"], create=True) as descriptor:
            name = parts[-1]
            try:
                existing = self._read(descriptor, name)
            except FileNotFoundError:
                existing = None
            except OSError as exc:
                raise ValueError("Workspace target is not a regular owned file") from exc
            if existing is not None:
                if hashlib.sha256(existing).hexdigest() != digest:
                    raise ValueError("Workspace target conflicts with existing content")
                return
            temporary = ".upload-" + uuid.uuid4().hex
            try:
                handle = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=descriptor,
                )
                with os.fdopen(handle, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Link atomically with no replacement; concurrent content cannot be overwritten.
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=descriptor,
                        dst_dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    if hashlib.sha256(self._read(descriptor, name)).hexdigest() != digest:
                        raise ValueError(
                            "Workspace target conflicts with concurrent content"
                        ) from None
            finally:
                os.unlink(temporary, dir_fd=descriptor)

    def export(self, session_id, attempt_id):
        self._identity(session_id)
        self._identity(attempt_id)
        output = []
        total = 0
        with self.directory([session_id, "artifacts", attempt_id], create=True) as descriptor:
            names = sorted(os.listdir(descriptor))
            if len(names) > 100:
                raise ValueError("Too many workspace artifacts")
            for name in names:
                self._parts(name)
                raw = self._read(descriptor, name)
                total += len(raw)
                if total > MAX_WORKSPACE_EXPORT_BYTES:
                    raise ValueError("Workspace output exceeds export limit")
                output.append(
                    {
                        "name": name,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size": len(raw),
                        "relative_path": f"artifacts/{attempt_id}/{name}",
                        "content_b64": base64.b64encode(raw).decode(),
                    }
                )
        return output


class WorkspaceTransfer:
    """Resolve catalog IDs before provider transfer and collect scoped artifacts."""

    def __init__(self, catalog, provider, artifacts, *, conversations=None):
        self.catalog = catalog
        self.provider = provider
        self.files = WorkspaceFiles(provider)
        self.artifacts = artifacts
        self.conversations = conversations

    def _owns_session(self, user_id, session_id):
        WorkspacePathPolicy.identifier(session_id, "session ID")
        if (
            self.conversations is not None
            and self.conversations.project_id_for_session(user_id, session_id) is None
        ):
            raise ContextNotFound("Session not found")

    async def prepare(self, user_id, session_id, file_ids) -> list[WorkspaceFileRef]:
        self._owns_session(user_id, session_id)
        files = {file.id: file for file in self.catalog.files_for(user_id)}
        payloads = []
        for file_id in dict.fromkeys(file_ids):
            file = files.get(file_id)
            raw = self.catalog.file_bytes_for(user_id, file_id) if file else None
            if file is None or raw is None:
                raise ContextNotFound("File not found")
            if len(raw) > MAX_WORKSPACE_FILE_BYTES:
                raise ValueError("Workspace file exceeds limit")
            digest = hashlib.sha256(raw).hexdigest()
            target = WorkspacePathPolicy.upload(session_id, file_id, file.name, digest)
            ref = WorkspaceFileRef(
                id=file_id,
                name=file.name,
                relative_path=target.logical,
                sha256=digest,
                size=len(raw),
            )
            payloads.append((ref, raw))
        context = WorkspaceOperationContext(
            user_id=user_id, session_id=session_id, attempt_id="transfer"
        )
        for ref, raw in payloads:
            await self.files.put_upload(
                context,
                file_id=ref.id,
                name=ref.name,
                content=raw,
                digest=ref.sha256,
            )
        return [ref for ref, _ in payloads]

    async def collect(self, user_id, session_id, attempt_id):
        self._owns_session(user_id, session_id)
        WorkspacePathPolicy.identifier(attempt_id, "attempt ID")
        context = WorkspaceOperationContext(
            user_id=user_id, session_id=session_id, attempt_id=attempt_id
        )
        directory = WorkspacePathPolicy.artifact(session_id, attempt_id).logical
        try:
            entries = await self.files.list(context, directory, max_depth=1, max_entries=100)
        except WorkspaceNotFound:
            return []
        if len(entries) > 100:
            raise ValueError("Too many workspace artifacts")
        verified = []
        total = 0
        for item in entries:
            if item.kind != "file":
                raise ValueError("Workspace artifact is not a regular file")
            prefix = f"artifacts/{attempt_id}/"
            if not item.path.startswith(prefix) or "/" in item.path[len(prefix) :]:
                raise ValueError("Invalid artifact path")
            name = item.path[len(prefix) :]
            if len(LocalWorkspace._parts(name)) != 1 or item.size > MAX_WORKSPACE_FILE_BYTES:
                raise ValueError("Workspace artifact exceeds limit")
            raw = await self.files.read_all(context, item.path, max_bytes=MAX_WORKSPACE_FILE_BYTES)
            total += len(raw)
            if (
                len(raw) > MAX_WORKSPACE_FILE_BYTES
                or total > MAX_WORKSPACE_EXPORT_BYTES
                or len(raw) != item.size
            ):
                raise ValueError("Invalid workspace artifact")
            verified.append((name, raw))
        return [
            self.artifacts.put(user_id, session_id, attempt_id, name, raw) for name, raw in verified
        ]
