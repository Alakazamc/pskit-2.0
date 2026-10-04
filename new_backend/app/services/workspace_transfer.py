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
    """Resolve catalog IDs before forwarding bytes; register only scoped output artifacts."""

    def __init__(self, catalog, runner, artifacts, *, conversations=None):
        self.catalog = catalog
        self.runner = runner
        self.artifacts = artifacts
        self.conversations = conversations

    def _owns_session(self, user_id, session_id):
        LocalWorkspace._identity(session_id)
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
            # Display names never determine filesystem paths.
            suffix = Path(file.name).suffix
            safe_id = hashlib.sha256(file_id.encode()).hexdigest()[:24]
            suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix) else ""
            ref = WorkspaceFileRef(
                id=file_id,
                name=file.name,
                relative_path=f"files/{safe_id}-{digest[:16]}{suffix}",
                sha256=digest,
                size=len(raw),
            )
            payloads.append((ref, raw))
        for ref, raw in payloads:
            await self.runner.workspace_request(
                user_id,
                "POST",
                "/v1/workspace/files",
                json={
                    "session_id": session_id,
                    "relative_path": ref.relative_path,
                    "sha256": ref.sha256,
                    "content_b64": base64.b64encode(raw).decode(),
                },
            )
        return [ref for ref, _ in payloads]

    async def collect(self, user_id, session_id, attempt_id):
        self._owns_session(user_id, session_id)
        LocalWorkspace._identity(attempt_id)
        data = await self.runner.workspace_request(
            user_id,
            "GET",
            "/v1/workspace/artifacts",
            params={"session_id": session_id, "attempt_id": attempt_id},
            continuation_attempt_id=attempt_id,
        )
        entries = data["files"]
        if len(entries) > 100:
            raise ValueError("Too many workspace artifacts")
        verified = []
        total = 0
        for item in entries:
            name = item["name"]
            if (
                len(LocalWorkspace._parts(name)) != 1
                or item["relative_path"] != f"artifacts/{attempt_id}/{name}"
            ):
                raise ValueError("Invalid artifact path")
            if len(item["content_b64"]) > MAX_WORKSPACE_FILE_BYTES * 4 // 3 + 8:
                raise ValueError("Workspace artifact exceeds limit")
            raw = base64.b64decode(item["content_b64"], validate=True)
            total += len(raw)
            if (
                len(raw) > MAX_WORKSPACE_FILE_BYTES
                or total > MAX_WORKSPACE_EXPORT_BYTES
                or (hashlib.sha256(raw).hexdigest() != item["sha256"] or len(raw) != item["size"])
            ):
                raise ValueError("Invalid workspace artifact")
            verified.append((name, raw))
        return [
            self.artifacts.put(user_id, session_id, attempt_id, name, raw) for name, raw in verified
        ]
