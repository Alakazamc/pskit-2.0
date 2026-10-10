"""Bounded file operations over a provider-neutral user workspace."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import time
from dataclasses import dataclass

from app.domain.workspace_paths import WorkspacePath, WorkspacePathPolicy
from app.ports.workspace_sandbox import (
    WorkspaceConflict,
    WorkspaceFileEntry,
    WorkspaceInvalidPath,
    WorkspaceNotFound,
    WorkspaceSandboxProvider,
    WorkspaceWriteResult,
)


@dataclass(frozen=True, slots=True)
class WorkspaceOperationContext:
    user_id: str
    session_id: str
    attempt_id: str


@dataclass(frozen=True, slots=True)
class WorkspaceReadResult:
    path: str
    content: bytes
    size: int
    offset: int
    eof: bool
    revision: str | None
    binary: bool


@dataclass(frozen=True, slots=True)
class WorkspaceSearchMatch:
    path: str
    line: int | None = None
    text: str | None = None


class WorkspaceFiles:
    """Enforce Session scope, write roots and bounded traversal above a provider."""

    def __init__(
        self,
        provider: WorkspaceSandboxProvider,
        *,
        paths: WorkspacePathPolicy | None = None,
        max_read_bytes: int = 1_048_576,
        operation_timeout_seconds: float = 10,
    ) -> None:
        self.provider = provider
        self.paths = paths or WorkspacePathPolicy()
        self.max_read_bytes = max_read_bytes
        self.operation_timeout_seconds = operation_timeout_seconds

    async def put_upload(
        self,
        context: WorkspaceOperationContext,
        *,
        file_id: str,
        name: str,
        content: bytes,
        digest: str,
    ) -> WorkspacePath:
        if hashlib.sha256(content).hexdigest() != digest:
            raise WorkspaceConflict("Workspace upload digest changed")
        path = self.paths.upload(context.session_id, file_id, name, digest)
        sandbox = await self.provider.ensure_user(context.user_id)
        try:
            current = await self.read_all(context, path.logical, max_bytes=len(content))
        except WorkspaceNotFound:
            pass
        else:
            if current != content:
                raise WorkspaceConflict("Workspace upload path already has different content")
            return path
        await self.provider.files.write(sandbox, context.session_id, path.logical, content)
        return path

    async def read(
        self,
        context: WorkspaceOperationContext,
        path: str,
        *,
        offset: int = 0,
        limit: int = 65_536,
    ) -> WorkspaceReadResult:
        if limit > self.max_read_bytes:
            raise WorkspaceInvalidPath("Workspace read exceeds the operation limit")
        resolved = self._scoped(context, path, writable=False)
        sandbox = await self.provider.ensure_user(context.user_id)
        chunk = await self.provider.files.read(
            sandbox, context.session_id, resolved.logical, offset=offset, limit=limit
        )
        return WorkspaceReadResult(
            path=resolved.logical,
            content=chunk.content,
            size=offset + len(chunk.content),
            offset=offset,
            eof=chunk.eof,
            revision=chunk.revision,
            binary=b"\x00" in chunk.content,
        )

    async def read_all(
        self,
        context: WorkspaceOperationContext,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        if max_bytes < 0:
            raise WorkspaceInvalidPath("Workspace read limit is invalid")
        output = bytearray()
        offset = 0
        revision: str | None = None
        while True:
            if offset > max_bytes:
                raise WorkspaceInvalidPath("Workspace file exceeds the operation limit")
            remaining = max_bytes - offset
            limit = min(self.max_read_bytes, remaining) if remaining else 1
            item = await self.read(context, path, offset=offset, limit=limit)
            if revision is not None and item.revision != revision:
                raise WorkspaceConflict("Workspace file changed during read")
            revision = item.revision
            output.extend(item.content)
            offset += len(item.content)
            if item.eof:
                return bytes(output)
            if not item.content or offset >= max_bytes:
                raise WorkspaceInvalidPath("Workspace file exceeds the operation limit")

    async def write(
        self,
        context: WorkspaceOperationContext,
        path: str,
        content: bytes,
        *,
        expected_revision: str | None = None,
    ) -> WorkspaceWriteResult:
        resolved = self._scoped(context, path, writable=True)
        sandbox = await self.provider.ensure_user(context.user_id)
        return await self.provider.files.write(
            sandbox,
            context.session_id,
            resolved.logical,
            content,
            expected_revision=expected_revision,
        )

    async def edit(
        self,
        context: WorkspaceOperationContext,
        path: str,
        *,
        old_text: str,
        new_text: str,
        expected_revision: str,
        replace_all: bool = False,
    ) -> WorkspaceWriteResult:
        current = await self.read(context, path, limit=self.max_read_bytes)
        if current.binary or not current.eof or current.revision != expected_revision:
            raise WorkspaceConflict("Workspace file changed before edit")
        try:
            text = current.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceInvalidPath("Workspace edit requires UTF-8 text") from exc
        occurrences = text.count(old_text)
        if occurrences == 0 or (occurrences > 1 and not replace_all):
            raise WorkspaceConflict("Workspace edit target is not unique")
        updated = text.replace(old_text, new_text, -1 if replace_all else 1).encode()
        return await self.write(
            context,
            path,
            updated,
            expected_revision=expected_revision,
        )

    async def list(
        self,
        context: WorkspaceOperationContext,
        path: str = ".",
        *,
        max_depth: int = 2,
        max_entries: int = 200,
    ) -> tuple[WorkspaceFileEntry, ...]:
        root = self._root_or_scoped(context, path)
        sandbox = await self.provider.ensure_user(context.user_id)
        deadline = time.monotonic() + self.operation_timeout_seconds
        pending = [(root, 0)]
        output: list[WorkspaceFileEntry] = []
        while pending:
            if time.monotonic() >= deadline:
                raise WorkspaceInvalidPath("Workspace listing timed out")
            current, depth = pending.pop(0)
            entries = await asyncio.wait_for(
                self.provider.files.list(sandbox, context.session_id, current),
                timeout=max(0.1, deadline - time.monotonic()),
            )
            for entry in entries:
                if entry.kind not in {"file", "directory"}:
                    raise WorkspaceInvalidPath("Workspace contains a special file")
                logical = entry.path
                self._scoped(context, logical, writable=False)
                output.append(entry)
                if len(output) > max_entries:
                    raise WorkspaceInvalidPath("Workspace listing exceeds the entry limit")
                if entry.kind == "directory" and depth + 1 < max_depth:
                    pending.append((logical, depth + 1))
        return tuple(output)

    async def find(
        self,
        context: WorkspaceOperationContext,
        path: str,
        pattern: str,
        *,
        max_depth: int = 4,
        max_entries: int = 200,
    ) -> tuple[WorkspaceSearchMatch, ...]:
        if "/" in pattern or "\\" in pattern or len(pattern) > 256:
            raise WorkspaceInvalidPath("Workspace find pattern is invalid")
        entries = await self.list(context, path, max_depth=max_depth, max_entries=max_entries)
        return tuple(
            WorkspaceSearchMatch(path=entry.path)
            for entry in entries
            if fnmatch.fnmatchcase(entry.path.rsplit("/", 1)[-1], pattern)
        )

    async def grep(
        self,
        context: WorkspaceOperationContext,
        path: str,
        pattern: str,
        *,
        max_depth: int = 4,
        max_matches: int = 100,
        max_bytes: int = 1_048_576,
    ) -> tuple[WorkspaceSearchMatch, ...]:
        if not pattern or len(pattern) > 256:
            raise WorkspaceInvalidPath("Workspace grep pattern is invalid")
        entries = await self.list(context, path, max_depth=max_depth, max_entries=1000)
        matches: list[WorkspaceSearchMatch] = []
        consumed = 0
        for entry in entries:
            if entry.kind != "file":
                continue
            remaining = max_bytes - consumed
            if remaining <= 0:
                break
            item = await self.read(context, entry.path, limit=min(remaining, self.max_read_bytes))
            consumed += len(item.content)
            if item.binary:
                continue
            text = item.content.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    matches.append(
                        WorkspaceSearchMatch(path=entry.path, line=number, text=line[:500])
                    )
                    if len(matches) >= max_matches:
                        return tuple(matches)
        return tuple(matches)

    def _root_or_scoped(self, context: WorkspaceOperationContext, path: str) -> str:
        if path == ".":
            WorkspacePathPolicy.identifier(context.session_id, "session ID")
            return "."
        return self._scoped(context, path, writable=False).logical

    def _scoped(
        self, context: WorkspaceOperationContext, path: str, *, writable: bool
    ) -> WorkspacePath:
        resolved = self.paths.logical(context.session_id, path, writable=writable)
        parts = resolved.logical.split("/")
        if parts[0] in {"attempts", "artifacts"} and (
            len(parts) < 2 or parts[1] != context.attempt_id
        ):
            raise WorkspaceInvalidPath("Workspace attempt path is out of scope")
        if writable and parts[0] == "files":
            raise WorkspaceInvalidPath("Uploaded files are read-only")
        return resolved
