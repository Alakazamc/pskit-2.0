"""SQLite snapshots and offline workspace bundles for the research agent."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath


def _copy_database(source: Path, destination: Path, *, force: bool) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Source and destination must not be the same path")
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists() and not force:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.",
                                           suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source)) as current, closing(sqlite3.connect(temporary)) as target:
            current.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError(f"Backup integrity check failed: {result}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def backup_database(source: Path, destination: Path, *, force: bool = False) -> None:
    """Create a consistent SQLite snapshot while the source database is open."""
    _copy_database(source, destination, force=force)


def restore_database(backup: Path, destination: Path, *, force: bool = False) -> None:
    """Restore a snapshot. The application must be stopped before this is called."""
    _copy_database(backup, destination, force=force)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir() or root.is_symlink():
        raise FileNotFoundError(root)
    files: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Pi session backup cannot include symlinks")
        if path.is_file():
            files.append((path.relative_to(root).as_posix(), path))
    return files


def backup_workspace_bundle(database: Path, sessions: Path, destination: Path) -> None:
    """Bundle a SQLite snapshot with Pi sessions. Stop Pi before invoking this."""
    if not database.is_file():
        raise FileNotFoundError(database)
    source_files = _session_files(sessions)
    destination = destination.resolve()
    if destination.is_relative_to(sessions.resolve()) or destination == database.resolve():
        raise ValueError("Backup bundle cannot be inside the live session tree or replace the database")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        backup_database(database, staging / "agent.sqlite3")
        session_copy = staging / "pi-sessions"
        session_copy.mkdir()
        hashes: dict[str, str] = {}
        for relative, source in source_files:
            target = session_copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            hashes[relative] = _sha256_file(target)
        manifest = {
            "format": 1,
            "database_sha256": _sha256_file(staging / "agent.sqlite3"),
            "session_sha256": hashes,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_workspace_bundle(bundle: Path, database: Path, sessions: Path) -> None:
    """Verify both assets before writing. Stop the application before restoring."""
    if bundle.is_symlink():
        raise ValueError("Workspace bundle cannot be a symlink")
    bundle = bundle.resolve()
    if database.resolve().is_relative_to(bundle) or sessions.resolve().is_relative_to(bundle):
        raise ValueError("Restore targets must be outside the backup bundle")
    if (database.resolve().is_relative_to(sessions.resolve())
            or sessions.resolve().is_relative_to(database.resolve())):
        raise ValueError("Restore database and Pi session targets must not overlap")
    if database.exists():
        raise FileExistsError(database)
    if sessions.exists():
        raise FileExistsError(sessions)
    manifest_path = bundle / "manifest.json"
    backup = bundle / "agent.sqlite3"
    if manifest_path.is_symlink() or backup.is_symlink():
        raise ValueError("Workspace bundle cannot contain symlinked metadata or database")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("format") != 1
            or not isinstance(manifest.get("session_sha256"), dict)
            or not isinstance(manifest.get("database_sha256"), str)):
        raise ValueError("Unsupported workspace bundle manifest")
    if _sha256_file(backup) != manifest.get("database_sha256"):
        raise ValueError("Database checksum mismatch")
    session_root = bundle / "pi-sessions"
    files = dict(_session_files(session_root))
    hashes = manifest["session_sha256"]
    if set(files) != set(hashes):
        raise ValueError("Pi session checksum manifest does not match files")
    for relative, expected in hashes.items():
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or relative != path.as_posix()
                or not isinstance(expected, str) or _sha256_file(files[relative]) != expected):
            raise ValueError("Pi session checksum mismatch")
    with closing(sqlite3.connect(backup)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("Bundled database failed integrity check")
    database.parent.mkdir(parents=True, exist_ok=True)
    sessions.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{database.name}.", dir=database.parent)
    os.close(fd)
    temporary_db = Path(temp_name)
    temporary_sessions = Path(tempfile.mkdtemp(prefix=f".{sessions.name}.", dir=sessions.parent))
    try:
        restore_database(backup, temporary_db, force=True)
        for relative, source in files.items():
            target = temporary_sessions / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        os.replace(temporary_sessions, sessions)
        try:
            os.replace(temporary_db, database)
        except BaseException:
            shutil.rmtree(sessions)
            raise
    finally:
        temporary_db.unlink(missing_ok=True)
        shutil.rmtree(temporary_sessions, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("backup", "restore"):
        operation = subcommands.add_parser(command)
        operation.add_argument("source", type=Path)
        operation.add_argument("destination", type=Path)
        operation.add_argument("--force", action="store_true")
    backup_bundle = subcommands.add_parser("backup-bundle")
    backup_bundle.add_argument("database", type=Path)
    backup_bundle.add_argument("pi_sessions", type=Path)
    backup_bundle.add_argument("bundle", type=Path)
    restore_bundle = subcommands.add_parser("restore-bundle")
    restore_bundle.add_argument("bundle", type=Path)
    restore_bundle.add_argument("database", type=Path)
    restore_bundle.add_argument("pi_sessions", type=Path)
    args = parser.parse_args()
    if args.command == "backup":
        backup_database(args.source, args.destination, force=args.force)
    elif args.command == "restore":
        restore_database(args.source, args.destination, force=args.force)
    elif args.command == "backup-bundle":
        backup_workspace_bundle(args.database, args.pi_sessions, args.bundle)
    else:
        restore_workspace_bundle(args.bundle, args.database, args.pi_sessions)


if __name__ == "__main__":
    main()
