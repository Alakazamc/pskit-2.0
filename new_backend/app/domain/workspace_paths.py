"""Pure path policy for one Session inside a user workspace."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.ports.workspace_sandbox import WorkspaceInvalidPath

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
_WRITABLE_ROOTS = frozenset({"work", "attempts", "artifacts"})


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """One canonical path in both user and provider views."""

    session_id: str
    logical: str
    physical: str


class WorkspacePathPolicy:
    """Construct canonical paths without touching a filesystem."""

    @staticmethod
    def identifier(value: str, label: str = "identifier") -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise WorkspaceInvalidPath(f"Workspace {label} is invalid")
        return value

    @classmethod
    def logical(cls, session_id: str, value: str, *, writable: bool = False) -> WorkspacePath:
        session = cls.identifier(session_id, "session ID")
        if not value or value.startswith(("/", "\\")) or "\x00" in value or "\\" in value:
            raise WorkspaceInvalidPath()
        parsed = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in parsed.parts):
            raise WorkspaceInvalidPath()
        logical = parsed.as_posix()
        if writable and parsed.parts[0] not in _WRITABLE_ROOTS:
            raise WorkspaceInvalidPath("Workspace path is read-only")
        return WorkspacePath(
            session_id=session,
            logical=logical,
            physical=f"/workspace/{session}/{logical}",
        )

    @classmethod
    def upload(cls, session_id: str, file_id: str, name: str, digest: str) -> WorkspacePath:
        file_key = cls.identifier(file_id, "file ID")
        if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise WorkspaceInvalidPath("Workspace file digest is invalid")
        display = cls.safe_name(name)
        stable_directory = hashlib.sha256(f"{file_key}:{digest}".encode()).hexdigest()[:32]
        return cls.logical(session_id, f"files/{stable_directory}/{display}")

    @classmethod
    def work(cls, session_id: str, path: str) -> WorkspacePath:
        return cls.logical(session_id, f"work/{path}", writable=True)

    @classmethod
    def attempt(cls, session_id: str, attempt_id: str, path: str = ".") -> WorkspacePath:
        attempt = cls.identifier(attempt_id, "attempt ID")
        value = f"attempts/{attempt}" if path == "." else f"attempts/{attempt}/{path}"
        return cls.logical(session_id, value, writable=True)

    @classmethod
    def artifact(cls, session_id: str, attempt_id: str, path: str = ".") -> WorkspacePath:
        attempt = cls.identifier(attempt_id, "attempt ID")
        value = f"artifacts/{attempt}" if path == "." else f"artifacts/{attempt}/{path}"
        return cls.logical(session_id, value, writable=True)

    @staticmethod
    def safe_name(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized or len(normalized.encode("utf-8")) > 180:
            raise WorkspaceInvalidPath("Workspace display name is invalid")
        suffix = PurePosixPath(normalized).suffix
        stem = normalized[: -len(suffix)] if suffix else normalized
        suffix = suffix if _SAFE_SUFFIX.fullmatch(suffix) else ""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
        if not stem:
            stem = "file"
        return stem[:128] + suffix.lower()
