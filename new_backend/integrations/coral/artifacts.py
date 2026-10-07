"""Standalone CORAL MCP artifact reader; install as pskit_mcp/artifacts.py on the provider."""

import base64
import hashlib
import mimetypes
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 512 * 1024


class CoralArtifacts:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS artifacts "
                       "(id TEXT PRIMARY KEY,path TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL)")

    @contextmanager
    def _database(self):
        db = sqlite3.connect(self.root / ".pskit-artifacts.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _path(self, relative):
        path = self.root / relative
        resolved = path.resolve()
        if (not resolved.is_relative_to(self.root) or path.is_symlink()
                or any(parent.is_symlink() for parent in path.parents if parent != self.root)
                or not path.is_file()):
            raise ValueError("ARTIFACT_PATH_INVALID")
        return path

    def register(self, path, workspace):
        workspace = Path(workspace).resolve()
        if workspace.parent != self.root or not re.fullmatch("[0-9a-f]{32}", workspace.name):
            raise ValueError("ARTIFACT_WORKSPACE_INVALID")
        relative = Path(path).absolute().relative_to(self.root)
        path = self._path(relative)
        if not path.resolve().is_relative_to(workspace):
            raise ValueError("ARTIFACT_PATH_INVALID")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError("ARTIFACT_TOO_LARGE")
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        artifact_id = "provider-" + hashlib.sha256(str(relative).encode()).hexdigest()[:48]
        with self._database() as db:
            for opaque in (artifact_id, "provider-" + digest[:24]):
                db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)",
                           (opaque, str(relative), size, digest))
        return {"id": artifact_id, "name": path.name,
                "kind": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "available": True, "size": size, "sha256": digest}

    def _legacy(self, artifact_id):
        # One bounded scan upgrades existing provider outputs without running a model.
        if not re.fullmatch("provider-[0-9a-f]{24}", artifact_id):
            return
        remaining = 10000
        for directory in self.root.iterdir():
            if directory.is_symlink() or not re.fullmatch("[0-9a-f]{32}", directory.name):
                continue
            for path in directory.rglob("*"):
                remaining -= 1
                if remaining < 0:
                    raise ValueError("ARTIFACT_INDEX_LIMIT")
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE_BYTES:
                    try:
                        ref = self.register(path, directory)
                    except ValueError:
                        continue
                    if "provider-" + ref["sha256"][:24] == artifact_id:
                        return

    def read(self, artifact_id, offset=0, length=MAX_CHUNK_BYTES):
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                or not isinstance(length, int) or isinstance(length, bool)
                or not 1 <= length <= MAX_CHUNK_BYTES
                or not re.fullmatch("provider-[0-9a-f]{24,48}", artifact_id)):
            raise ValueError("ARTIFACT_REQUEST_INVALID")
        with self._database() as db:
            row = db.execute("SELECT path,size,sha256 FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            self._legacy(artifact_id)
            with self._database() as db:
                row = db.execute("SELECT path,size,sha256 FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise ValueError("ARTIFACT_NOT_FOUND")
        path = self._path(row[0])
        if offset > row[1] or path.stat().st_size != row[1]:
            raise ValueError("ARTIFACT_CHANGED")
        with path.open("rb") as file:
            file.seek(offset)
            data = file.read(min(length, row[1] - offset))
        return {"artifact_id": artifact_id, "offset": offset, "size": row[1],
                "sha256": row[2], "data_base64": base64.b64encode(data).decode(),
                "eof": offset + len(data) == row[1]}
