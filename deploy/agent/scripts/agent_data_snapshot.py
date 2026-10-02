"""Copy a stopped Agent volume with SQLite WAL folded into a verified database.

The caller must stop the old backend before snapshotting so Pi session files
cannot change while they are copied. This script never handles Supabase data.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile


DATABASE = "agent.sqlite3"
MANIFEST = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_links(root: Path) -> None:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("symbolic links are not allowed in an Agent snapshot")


def _verify_sqlite(path: Path) -> None:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Agent SQLite integrity check failed")


def snapshot(source: Path, destination: Path) -> dict[str, str]:
    """Snapshot a stopped volume; destination must not already exist."""
    source, destination = Path(source), Path(destination)
    database = source / DATABASE
    if not source.is_dir() or not database.is_file():
        raise ValueError("source must contain agent.sqlite3")
    _reject_links(source)
    if destination.exists() or destination.is_symlink():
        raise ValueError("snapshot destination must not exist")
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("snapshot destination must be outside the source volume")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".agent-snapshot-", dir=destination.parent))
    try:
        # A read-only Docker volume may contain WAL without SHM. SQLite needs
        # writable space to reconstruct SHM, so work from a private copy.
        raw = staged / ".source"
        raw.mkdir(mode=0o700)
        raw_database = raw / DATABASE
        shutil.copy2(database, raw_database)
        raw_database.chmod(0o600)
        wal = source / f"{DATABASE}-wal"
        if wal.exists():
            shutil.copy2(wal, raw / wal.name)
            (raw / wal.name).chmod(0o600)
        backed_up = staged / DATABASE
        with sqlite3.connect(raw_database) as origin:
            with sqlite3.connect(backed_up) as output:
                origin.backup(output)
                # Keep the archive self-contained even when source uses WAL.
                output.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                output.execute("PRAGMA journal_mode=DELETE")
        shutil.rmtree(raw)
        _verify_sqlite(backed_up)
        backed_up.chmod(0o600)

        source_sessions = source / "pi-sessions"
        target_sessions = staged / "pi-sessions"
        if source_sessions.is_dir():
            shutil.copytree(source_sessions, target_sessions)
        else:
            target_sessions.mkdir()
        for path in target_sessions.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        target_sessions.chmod(0o700)

        files = {path.relative_to(staged).as_posix(): _sha256(path)
                 for path in staged.rglob("*") if path.is_file()}
        manifest = staged / MANIFEST
        manifest.write_text(json.dumps(files, sort_keys=True, indent=2) + "\n")
        manifest.chmod(0o600)
        staged.rename(destination)
        return files
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def restore(snapshot_dir: Path, target: Path) -> None:
    """Verify every file before copying into an empty target volume."""
    snapshot_dir, target = Path(snapshot_dir), Path(target)
    if target.is_symlink() or (target.exists() and
                               (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("restore target must be an empty directory")
    if not snapshot_dir.is_dir():
        raise ValueError("snapshot directory is missing")
    _reject_links(snapshot_dir)
    try:
        files = json.loads((snapshot_dir / MANIFEST).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot manifest is missing or invalid") from exc
    if not isinstance(files, dict) or DATABASE not in files:
        raise ValueError("snapshot manifest is invalid")
    actual = {path.relative_to(snapshot_dir).as_posix()
              for path in snapshot_dir.rglob("*") if path.is_file()}
    if actual != set(files) | {MANIFEST}:
        raise ValueError("snapshot contains unverified or missing files")
    for relative, expected in files.items():
        if not isinstance(relative, str):
            raise ValueError("snapshot manifest contains an unsafe entry")
        path = PurePosixPath(relative)
        if (path.is_absolute()
                or any(part in (".", "..") for part in path.parts)
                or "\\" in relative
                or (relative != DATABASE and not relative.startswith("pi-sessions/"))
                or not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ValueError("snapshot manifest contains an unsafe entry")
        if not hmac.compare_digest(_sha256(snapshot_dir / relative), expected):
            raise ValueError(f"snapshot checksum mismatch: {relative}")
    _verify_sqlite(snapshot_dir / DATABASE)

    target.mkdir(parents=True, exist_ok=True)
    (target / "pi-sessions").mkdir(mode=0o700, exist_ok=True)
    for relative in sorted(files):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_dir / relative, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    save = actions.add_parser("snapshot")
    save.add_argument("source", type=Path)
    save.add_argument("destination", type=Path)
    save.add_argument("--source-stopped", action="store_true", required=True)
    load = actions.add_parser("restore")
    load.add_argument("snapshot_dir", type=Path)
    load.add_argument("target", type=Path)
    args = parser.parse_args()
    if args.action == "snapshot":
        files = snapshot(args.source, args.destination)
        print(f"Agent snapshot complete: {len(files)} verified files")
    else:
        restore(args.snapshot_dir, args.target)
        print("Agent restore complete")


if __name__ == "__main__":
    main()
