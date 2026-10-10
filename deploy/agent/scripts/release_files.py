"""Verified artifact snapshots and atomic release files beneath pinned no-symlink directories."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path


class ReleaseError(RuntimeError):
    """Fail-closed operator error with no command output or secret values."""


@contextmanager
def directory_fd(path: Path):
    """Traverse every path component using openat/O_NOFOLLOW; pin the final directory."""
    parts = path.absolute().parts
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:]:
            if part in {".", ".."}:
                raise ReleaseError("Unsafe directory path")
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def safe_read(path: Path, *, private: bool = False, limit: int = 1024 * 1024) -> bytes:
    with directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or private and before.st_mode & 0o077:
                raise ReleaseError("A regular private operational file is required")
            if before.st_size > limit:
                raise ReleaseError("Operational file exceeds its size limit")
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(content) > limit or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ReleaseError("Operational file changed while reading")
            return content


def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    with directory_fd(path.parent) as parent:
        atomic_at(parent, path.name, content, mode=mode)


def atomic_at(parent: int, name: str, content: bytes, *, mode: int = 0o644) -> None:
    temporary = ".pskit-release-" + uuid.uuid4().hex
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ReleaseError("Refusing to replace a nonregular release file")
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def snapshot_dist(path: Path, expected_hash: str) -> dict[str, bytes]:
    """Hash the bytes actually copied, using the single release_manifest dist algorithm."""
    files: dict[str, bytes] = {}
    total = 0

    def walk(fd: int, prefix: str = "") -> None:
        nonlocal total
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            relative = prefix + name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                try:
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if before.st_size + total > 512 * 1024 * 1024:
                        raise ReleaseError(
                            "Frontend artifact exceeds the 512 MiB release limit"
                        )
                    value = stream.read(512 * 1024 * 1024 - total + 1)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns) != (
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise ReleaseError("Frontend artifact changed during snapshot")
                    total += len(value)
                    if total > 512 * 1024 * 1024:
                        raise ReleaseError(
                            "Frontend artifact exceeds the release limit"
                        )
                    files[relative] = value
            else:
                raise ReleaseError(
                    "Frontend artifact contains a symlink or special file"
                )

    with directory_fd(path) as fd:
        walk(fd)
    if "index.html" not in files or any(name.startswith(".pskit-") for name in files):
        raise ReleaseError(
            "Frontend artifact lacks index or contains reserved control files"
        )
    digest = hashlib.sha256()
    for name, value in sorted(files.items()):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(hashlib.sha256(value).digest())
    if digest.hexdigest() != expected_hash:
        raise ReleaseError("Frontend artifact fingerprint differs from the Manifest")
    return files


@contextmanager
def relative_parent(root: int, relative: str, *, create: bool = False):
    """Keep static writes under a pinned root even if another process replaces a directory."""
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseError("Unsafe frontend asset path")
    current = os.dup(root)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = child
        yield current, parts[-1]
    finally:
        os.close(current)


def read_at(parent: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ReleaseError("Static asset is not a regular file")
        return stream.read()


def publish_assets(root: int, files: dict[str, bytes]) -> None:
    """Never overwrite existing assets; same name must already contain identical bytes."""
    for relative, content in files.items():
        if relative == "index.html":
            continue
        with relative_parent(root, relative, create=True) as (parent, name):
            try:
                existing = read_at(parent, name)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if existing != content:
                    raise ReleaseError(
                        "Frontend asset name collision; use content hashed names"
                    )
                continue
            temporary = ".pskit-asset-" + uuid.uuid4().hex
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=parent,
                        dst_dir_fd=parent,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    if read_at(parent, name) != content:
                        raise ReleaseError(
                            "Concurrent frontend asset collision"
                        ) from None
                os.fsync(parent)
            finally:
                os.unlink(temporary, dir_fd=parent)
