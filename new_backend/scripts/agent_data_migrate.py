"""Offline Agent data migration between a stopped SQLite snapshot and PostgreSQL.

Only the private PSKit schema is read or changed. The source SQLite file and
snapshot manifest remain untouched. Stop all Agent writers before using this
module; schema activation refuses a destination with existing business rows.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg
from psycopg import sql

from app.db.migrations.components import (
    migrate_catalog_schema,
    migrate_identity_policy_schema,
    migrate_internal_tool_auth_schema,
    migrate_mcp_capacity_schema,
    migrate_mcp_tool_calls_schema,
    migrate_oauth_schema,
    migrate_pdf_capacity_schema,
    migrate_tool_run_schema,
)
from app.db.postgres_migrations import migrate_postgres
from app.domain.guest_rate_limit import GuestRateLimiter
from app.domain.persistent_conversation import PersistentConversationStore

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PI_ROOT = Path("/data/pi-sessions")
_SQLITE_METADATA = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}
_MIGRATION_ONLY = {"schema_migrations"}
_POSTGRES_COMPUTE = {
    "compute_capability_versions", "compute_job_data", "compute_reservations",
    "compute_usage_reports", "compute_usage_daily", "compute_cpu_limits",
    "compute_device_leases", "compute_result_receipts", "compute_outbox",
}


@dataclass(frozen=True)
class ImportReport:
    """Non-sensitive evidence for an activated PostgreSQL import."""

    table_counts: dict[str, int]
    source_sha256: str
    backup_schema: str | None


@dataclass(frozen=True)
class ExportReport:
    """Non-sensitive evidence for a new SQLite rollback file."""

    table_counts: dict[str, int]
    destination_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identifier(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise ValueError("Unsafe migration schema or table name")
    return name


def _verify_snapshot(snapshot_dir: Path) -> set[str]:
    """Verify a stopped-volume manifest before copying any transcript bytes."""
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        raise ValueError("Agent snapshot directory is unsafe or missing")
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("Agent snapshot manifest is missing")
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Agent snapshot manifest is invalid") from exc
    if not isinstance(entries, dict) or "agent.sqlite3" not in entries:
        raise ValueError("Agent snapshot manifest is invalid")
    actual: set[str] = set()
    for path in snapshot_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError("Agent snapshot contains a symbolic link")
        if path.is_file():
            actual.add(path.relative_to(snapshot_dir).as_posix())
    if actual != set(entries) | {"manifest.json"}:
        raise ValueError("Agent snapshot file list does not match its manifest")
    for relative, expected in entries.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("Agent snapshot manifest contains an invalid entry")  # noqa: TRY004
        candidate = PurePosixPath(relative)
        if (candidate.is_absolute() or ".." in candidate.parts or "\\" in relative
                or (relative != "agent.sqlite3" and not relative.startswith("pi-sessions/"))
                or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ValueError("Agent snapshot manifest contains an unsafe entry")
        if not hmac.compare_digest(_sha256(snapshot_dir / relative), expected):
            raise ValueError("Agent snapshot checksum mismatch")
    return set(entries)


def copy_pi_transcripts(snapshot_dir: Path, destination: Path) -> int:
    """Copy verified Pi files into a fresh persistent directory atomically."""
    snapshot_dir, destination = Path(snapshot_dir), Path(destination)
    files = _verify_snapshot(snapshot_dir)
    if destination.is_symlink() or (destination.exists() and
                                    (not destination.is_dir() or any(destination.iterdir()))):
        raise FileExistsError(destination)
    mounted_empty = destination.is_dir()
    if destination.resolve().is_relative_to(snapshot_dir.resolve()):
        raise ValueError("Pi destination must be outside the snapshot")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(
        prefix=".pi-import-", dir=destination if mounted_empty else destination.parent,
    ))
    try:
        for relative in sorted(files):
            if not relative.startswith("pi-sessions/"):
                continue
            part = PurePosixPath(relative).relative_to("pi-sessions")
            target = staged.joinpath(*part.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_dir / relative, target)
            target.chmod(0o600)
        for directory in staged.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o700)
        staged.chmod(0o700)
        if mounted_empty:
            moved: list[Path] = []
            try:
                for child in staged.iterdir():
                    target = destination / child.name
                    child.rename(target)
                    moved.append(target)
            except BaseException:
                for path in moved:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                raise
        else:
            staged.rename(destination)
        return sum(name.startswith("pi-sessions/") for name in files)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def _sqlite_copy(source: Path, destination: Path) -> None:
    """Copy a validated snapshot to scratch before running legacy migrations."""
    with (closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as old,
          closing(sqlite3.connect(destination)) as current, current):
        old.backup(current)
        if current.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Agent SQLite integrity check failed")


def _upgrade_sqlite_copy(path: Path) -> None:
    """Materialize optional component tables in a disposable SQLite copy."""
    store = PersistentConversationStore(str(path))
    try:
        for migrate in (
            migrate_catalog_schema, migrate_identity_policy_schema,
            migrate_internal_tool_auth_schema, migrate_mcp_capacity_schema,
            migrate_mcp_tool_calls_schema, migrate_oauth_schema,
            migrate_pdf_capacity_schema, migrate_tool_run_schema,
        ):
            migrate(store.db)
    finally:
        store.db.close()
    limiter = GuestRateLimiter(str(path), secret="offline-migration-only", limit_per_hour=1)
    limiter.db.close()


def _table_names(connection: psycopg.Connection, schema: str) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_type='BASE TABLE'", (schema,),
    )}


def _columns(connection: psycopg.Connection, schema: str, table: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table),
    )]


def _mapped_pi_path(value: str, session_id: str, snapshot_dir: Path, pi_root: Path) -> str:
    """Map only a manifest-backed file in the session's own directory."""
    parts = PurePosixPath(value).parts
    if session_id not in parts:
        raise ValueError("Pi checkpoint is outside its session directory")
    relative = PurePosixPath(*parts[parts.index(session_id):])
    if ".." in relative.parts or len(relative.parts) < 2:
        raise ValueError("Pi checkpoint has an unsafe path")
    archived = snapshot_dir / "pi-sessions" / relative
    installed = pi_root / relative
    if not archived.is_file() or not installed.is_file():
        raise ValueError("Pi checkpoint is absent from the verified transcript copy")
    if not hmac.compare_digest(_sha256(archived), _sha256(installed)):
        raise ValueError("Pi checkpoint copy checksum mismatch")
    return str(installed)


def _canonical(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _digest_rows(rows: Iterable[tuple]) -> str:
    hashes = [hashlib.sha256(json.dumps(
        [_canonical(value) for value in row], ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode()).digest() for row in rows]
    return hashlib.sha256(b"".join(sorted(hashes))).hexdigest()


def _copy_rows(
    source: sqlite3.Connection, destination: psycopg.Connection,
    *, staging_schema: str, snapshot_dir: Path, pi_root: Path,
) -> dict[str, int]:
    """Copy every known SQLite table and verify canonical per-row hashes."""
    old_tables = {
        row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    } - _SQLITE_METADATA
    new_tables = _table_names(destination, staging_schema) - _MIGRATION_ONLY - _POSTGRES_COMPUTE
    if old_tables != new_tables:
        raise ValueError("SQLite and PostgreSQL table sets differ")
    counts: dict[str, int] = {}
    for table in sorted(old_tables):
        _identifier(table)
        source_columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
        target_columns = _columns(destination, staging_schema, table)
        if set(source_columns) != set(target_columns) - {"ordinal"}:
            raise ValueError(f"PostgreSQL table has no mapping for SQLite {table}")
        has_ordinal = "ordinal" in target_columns and "ordinal" not in source_columns
        inserted_columns = [*source_columns, *(["ordinal"] if has_ordinal else [])]
        statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
            sql.Identifier(staging_schema), sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, inserted_columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in inserted_columns),
        )
        expected_hashes: list[bytes] = []
        count = 0
        source_cursor = source.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')
        for entry in source_cursor:
            rowid, values = entry[0], dict(zip(source_columns, entry[1:], strict=True))
            if table == "catalog_files":
                values["content"] = values["content"].replace("\x00", "\ufffd")
            if table == "pi_sessions":
                values["session_file"] = _mapped_pi_path(
                    values["session_file"], values["session_id"], snapshot_dir, pi_root,
                )
            if table == "agent_runs" and values.get("checkpoint_file"):
                values["checkpoint_file"] = _mapped_pi_path(
                    values["checkpoint_file"], values["session_id"], snapshot_dir, pi_root,
                )
            if has_ordinal:
                values["ordinal"] = rowid
            row = tuple(values[column] for column in inserted_columns)
            destination.execute(statement, row)
            expected_hashes.append(hashlib.sha256(json.dumps(
                [_canonical(value) for value in row], ensure_ascii=False,
                separators=(",", ":"), sort_keys=True,
            ).encode()).digest())
            count += 1
        selected = sql.SQL("SELECT {} FROM {}.{}").format(
            sql.SQL(",").join(map(sql.Identifier, inserted_columns)),
            sql.Identifier(staging_schema), sql.Identifier(table),
        )
        found = destination.execute(selected)
        if _digest_rows(found) != hashlib.sha256(b"".join(sorted(expected_hashes))).hexdigest():
            raise ValueError(f"PostgreSQL import checksum mismatch for {table}")
        counts[table] = count
    return counts


def _reset_sequences(connection: psycopg.Connection, schema: str) -> None:
    """Advance identity sequences beyond imported IDs and SQLite rowids."""
    identities = connection.execute(
        "SELECT table_name,column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND is_identity='YES'", (schema,),
    ).fetchall()
    for table, column in identities:
        maximum = connection.execute(sql.SQL("SELECT MAX({}) FROM {}.{}").format(
            sql.Identifier(column), sql.Identifier(schema), sql.Identifier(table),
        )).fetchone()[0]
        sequence = connection.execute(
            "SELECT pg_get_serial_sequence(%s,%s)", (f"{schema}.{table}", column),
        ).fetchone()[0]
        if sequence is None:
            raise ValueError("PostgreSQL identity sequence is unavailable")
        connection.execute(
            "SELECT setval(%s,%s,%s)", (sequence, maximum or 1, maximum is not None),
        )


def _activate_stage(
    connection: psycopg.Connection, *, stage: str, target: str,
) -> str | None:
    """Swap the verified stage into service in one PostgreSQL transaction."""
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"pskit-activate:{target}",),
    )
    exists = connection.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target,),
    ).fetchone()
    backup = None
    if exists:
        for table in _table_names(connection, target) - _MIGRATION_ONLY:
            rows = connection.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 1").format(
                sql.Identifier(target), sql.Identifier(table),
            )).fetchone()
            if rows:
                raise ValueError("Active PSKit schema contains business rows; freeze and reconcile first")
        backup = f"{target}_preimport_{uuid.uuid4().hex[:8]}"
        connection.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
            sql.Identifier(target), sql.Identifier(backup),
        ))
    connection.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
        sql.Identifier(stage), sql.Identifier(target),
    ))
    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname='pskit_app'").fetchone():
        role = sql.Identifier("pskit_app")
        namespace = sql.Identifier(target)
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(namespace, role))
        connection.execute(sql.SQL(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}"
        ).format(namespace, role))
        connection.execute(sql.SQL(
            "GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA {} TO {}"
        ).format(namespace, role))
    return backup


def import_sqlite_snapshot(
    sqlite_path: Path, database_url: str, *, staging_schema: str = "pskit_stage",
    target_schema: str = "pskit", pi_root: Path = _PI_ROOT,
) -> ImportReport:
    """Verify, stage, and atomically activate a stopped Agent SQLite snapshot."""
    sqlite_path = Path(sqlite_path)
    if sqlite_path.is_symlink() or sqlite_path.parent.is_symlink():
        raise ValueError("Agent SQLite snapshot cannot be a symbolic link")
    sqlite_path = sqlite_path.resolve()
    pi_root = Path(pi_root).resolve()
    staging_schema, target_schema = _identifier(staging_schema), _identifier(target_schema)
    if staging_schema == target_schema or not sqlite_path.is_file() or sqlite_path.is_symlink():
        raise ValueError("Invalid migration source or schema names")
    snapshot_dir = sqlite_path.parent
    _verify_snapshot(snapshot_dir)
    source_hash = _sha256(sqlite_path)
    with psycopg.connect(database_url) as admin:
        if admin.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (staging_schema,),
        ).fetchone():
            raise ValueError("Staging schema already exists; inspect and remove it before retry")
    with tempfile.TemporaryDirectory(prefix="agent-sqlite-import-") as scratch:
        disposable = Path(scratch) / "agent.sqlite3"
        _sqlite_copy(sqlite_path, disposable)
        if not hmac.compare_digest(_sha256(sqlite_path), source_hash):
            raise ValueError("Agent snapshot changed during import")
        _upgrade_sqlite_copy(disposable)
        try:
            migrate_postgres(database_url, schema=staging_schema)
            with sqlite3.connect(disposable) as source, psycopg.connect(database_url) as connection:
                counts = _copy_rows(
                    source, connection, staging_schema=staging_schema,
                    snapshot_dir=snapshot_dir, pi_root=pi_root,
                )
                _reset_sequences(connection, staging_schema)
            with psycopg.connect(database_url) as connection:
                backup = _activate_stage(connection, stage=staging_schema, target=target_schema)
        except BaseException:
            with psycopg.connect(database_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(staging_schema)
                ))
            raise
    return ImportReport(table_counts=counts, source_sha256=source_hash, backup_schema=backup)


def export_sqlite_snapshot(
    database_url: str, destination: Path, *, schema: str = "pskit",
) -> ExportReport:
    """Write current private PostgreSQL state to a new offline SQLite file."""
    schema, destination = _identifier(schema), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".sqlite3", dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    counts: dict[str, int] = {}
    try:
        _upgrade_sqlite_copy(temporary)
        with (psycopg.connect(database_url) as source,
              closing(sqlite3.connect(temporary)) as target, target):
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            old_tables = {
                row[0] for row in target.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            } - _SQLITE_METADATA
            for table in _POSTGRES_COMPUTE:
                exists = source.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 1").format(
                    sql.Identifier(schema), sql.Identifier(table),
                )).fetchone()
                if exists:
                    raise ValueError("Compute state requires a PostgreSQL backup; SQLite export would lose data")
            new_tables = _table_names(source, schema) - _MIGRATION_ONLY - _POSTGRES_COMPUTE
            if old_tables != new_tables:
                raise ValueError("PostgreSQL and SQLite table sets differ")
            for table in old_tables:
                target.execute(f'DELETE FROM "{_identifier(table)}"')
            for table in sorted(old_tables):
                columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
                source_columns = _columns(source, schema, table)
                if set(columns) != set(source_columns) - {"ordinal"}:
                    raise ValueError(f"SQLite table has no mapping for PostgreSQL {table}")
                has_ordinal = "ordinal" in source_columns and "ordinal" not in columns
                selected = [*columns, *(["ordinal"] if has_ordinal else [])]
                query = sql.SQL("SELECT {} FROM {}.{}").format(
                    sql.SQL(",").join(map(sql.Identifier, selected)),
                    sql.Identifier(schema), sql.Identifier(table),
                )
                if has_ordinal:
                    query += sql.SQL(" ORDER BY ordinal")
                elif "id" in columns:
                    query += sql.SQL(" ORDER BY id")
                insert_columns = [*(["rowid"] if has_ordinal else []), *columns]
                placeholders = ",".join("?" for _ in insert_columns)
                quoted_columns = ",".join(f'"{column}"' for column in insert_columns)
                insert = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                count = 0
                for row in source.execute(query):
                    values = (row[-1], *row[:-1]) if has_ordinal else row
                    target.execute(insert, values)
                    count += 1
                counts[table] = count
        with closing(sqlite3.connect(temporary)) as verified:
            verified.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            verified.execute("PRAGMA journal_mode=DELETE")
            if verified.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Exported SQLite integrity check failed")
        temporary.chmod(0o600)
        temporary.rename(destination)
        return ExportReport(table_counts=counts, destination_sha256=_sha256(destination))
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Run an explicit offline import, transcript copy, or rollback export."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("copy-pi")
    prepare.add_argument("snapshot", type=Path)
    prepare.add_argument("destination", type=Path)
    incoming = commands.add_parser("import")
    incoming.add_argument("sqlite", type=Path)
    incoming.add_argument("--stage", default="pskit_stage")
    incoming.add_argument("--pi-root", type=Path, default=_PI_ROOT)
    outgoing = commands.add_parser("export")
    outgoing.add_argument("destination", type=Path)
    outgoing.add_argument("--schema", default="pskit")
    args = parser.parse_args(argv)
    database_url = os.getenv("RESEARCH_AGENT_DATABASE_URL", "")
    if args.action != "copy-pi" and not database_url:
        parser.error("RESEARCH_AGENT_DATABASE_URL must be provided through a protected environment file")
    if args.action == "copy-pi":
        files = copy_pi_transcripts(args.snapshot, args.destination)
        print(f"Pi transcript copy complete: {files} verified files")
    elif args.action == "import":
        report = import_sqlite_snapshot(
            args.sqlite, database_url, staging_schema=args.stage,
            pi_root=args.pi_root,
        )
        print(f"Agent import complete: {sum(report.table_counts.values())} rows, "
              f"{len(report.table_counts)} tables")
    else:
        report = export_sqlite_snapshot(database_url, args.destination, schema=args.schema)
        print(f"Agent export complete: {sum(report.table_counts.values())} rows, "
              f"{len(report.table_counts)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
