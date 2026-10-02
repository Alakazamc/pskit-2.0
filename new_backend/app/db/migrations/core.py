"""Core SQLite schema migrations."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from .common import call_add_column_if_missing as add_column_if_missing


SCHEMA_VERSION = 14

def _legacy_core_columns(db: sqlite3.Connection) -> None:
    """Backfill columns required by databases created before version stamps."""
    for name, declaration in (
        ("resume_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("retry_after", "TEXT"),
        ("context_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("lease_owner", "TEXT"),
        ("lease_expires_at", "TEXT"),
    ):
        add_column_if_missing(db, "agent_runs", name, declaration)
    add_column_if_missing(db, "agent_messages", "parts_json", "TEXT")

def _version_1(db: sqlite3.Connection) -> None:
    """Backfill legacy Run and message columns."""
    _legacy_core_columns(db)

def _version_2(db: sqlite3.Connection) -> None:
    """Add saved AF3 and approval inputs."""
    add_column_if_missing(db, "agent_jobs", "input_json", "TEXT")
    add_column_if_missing(db, "agent_approvals", "input_json", "TEXT")

def _version_3(db: sqlite3.Connection) -> None:
    """Add compute worker lease and attempt columns."""
    add_column_if_missing(db, "agent_jobs", "worker_id", "TEXT")
    add_column_if_missing(db, "agent_jobs", "lease_expires_at", "TEXT")
    add_column_if_missing(db, "agent_jobs", "attempts", "INTEGER NOT NULL DEFAULT 0")

def _version_4(db: sqlite3.Connection) -> None:
    """Add fencing tokens to compute leases."""
    add_column_if_missing(db, "agent_jobs", "lease_token", "TEXT")

def _version_5(db: sqlite3.Connection) -> None:
    """Record whether an AF3 job was simulated."""
    add_column_if_missing(db, "agent_jobs", "simulation", "INTEGER NOT NULL DEFAULT 0")

def _version_6(db: sqlite3.Connection) -> None:
    """Record the first compute claim and backfill active jobs."""
    add_column_if_missing(db, "agent_jobs", "first_claimed_at", "TEXT")
    db.execute(
        "UPDATE agent_jobs SET first_claimed_at=created_at "
        "WHERE status='running' AND first_claimed_at IS NULL"
    )

def _version_7(db: sqlite3.Connection) -> None:
    """Add durable Pi Run checkpoint and resume fields."""
    add_column_if_missing(db, "agent_runs", "initial_attempts", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(db, "agent_runs", "checkpoint_file", "TEXT")
    add_column_if_missing(db, "agent_runs", "last_resumed_job_id", "TEXT")
    add_column_if_missing(db, "agent_runs", "active_resume_job_id", "TEXT")
    add_column_if_missing(db, "agent_runs", "turn_start_seq", "INTEGER NOT NULL DEFAULT 0")
    db.execute(
        "UPDATE agent_runs SET checkpoint_file=(SELECT session_file FROM pi_sessions "
        "WHERE pi_sessions.session_id=agent_runs.session_id "
        "AND pi_sessions.user_id=agent_runs.user_id) "
        "WHERE status IN ('waiting','resume_queued') AND checkpoint_file IS NULL"
    )

def _version_8(db: sqlite3.Connection) -> None:
    """Record whether Token usage entries are posted or reserved."""
    add_column_if_missing(db, "agent_token_entries", "status", "TEXT NOT NULL DEFAULT 'posted'")

def _version_9(db: sqlite3.Connection) -> None:
    """Persist idempotency keys for public AF3 requests."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS agent_af3_request_keys ("
        "user_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, "
        "job_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,key))"
    )

def _version_10(db: sqlite3.Connection) -> None:
    """Persist per-model-call Token reservations."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS agent_model_call_guards ("
        "call_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, run_id TEXT NOT NULL, "
        "prompt_bytes INTEGER NOT NULL, max_output_tokens INTEGER NOT NULL, "
        "reserved_tokens INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)"
    )

def _version_11(db: sqlite3.Connection) -> None:
    """Add AF3 compute resource requirements."""
    add_column_if_missing(
        db, "agent_jobs", "resource_requirements_json",
        "TEXT NOT NULL DEFAULT '{\"capability\":\"af3\",\"gpu_count\":1,\"min_gpu_memory_mb\":0}'",
    )

def _version_12(db: sqlite3.Connection) -> None:
    """Classify existing AF3 GPU charges for reconciliation."""
    add_column_if_missing(
        db, "agent_jobs", "gpu_accounting_status", "TEXT NOT NULL DEFAULT 'reserved'",
    )
    db.execute(
        "UPDATE agent_jobs SET gpu_accounting_status=CASE "
        "WHEN status IN ('queued','running') THEN 'reserved' "
        "WHEN status IN ('completed','failed','cancelled') AND actual_minutes>0 "
        "THEN 'settled' "
        "WHEN status='cancelled' AND COALESCE(attempts,0)=0 THEN 'released' "
        "WHEN status='failed' AND COALESCE(attempts,0)=0 THEN 'released' "
        "WHEN status IN ('cancelled','failed') AND COALESCE(attempts,0)>0 "
        "AND COALESCE(actual_minutes,0)=0 THEN 'pending_reconciliation' "
        "ELSE 'settled' END"
    )
    db.execute(
        "UPDATE agent_jobs SET actual_minutes=NULL "
        "WHERE gpu_accounting_status='pending_reconciliation'"
    )

def _version_13(db: sqlite3.Connection) -> None:
    """Create an audit trail for AF3 GPU reconciliation."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS agent_gpu_reconciliations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, source TEXT NOT NULL, previous_minutes INTEGER, "
        "actual_minutes INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)"
    )

def _version_14(db: sqlite3.Connection) -> None:
    """Add project icons to existing workspaces."""
    add_column_if_missing(db, "workspace_projects", "icon", "TEXT NOT NULL DEFAULT 'folder'")

MIGRATIONS: tuple[tuple[str, Callable[[sqlite3.Connection], None]], ...] = (
    ("legacy core columns", _version_1),
    ("AF3 input contract", _version_2),
    ("compute leases", _version_3),
    ("compute lease token", _version_4),
    ("AF3 simulation provenance", _version_5),
    ("AF3 execution deadline", _version_6),
    ("durable initial run recovery", _version_7),
    ("model attempt usage status", _version_8),
    ("public AF3 idempotency keys", _version_9),
    ("per-model-call Token reservations", _version_10),
    ("AF3 compute resource requirements", _version_11),
    ("AF3 GPU usage reconciliation", _version_12),
    ("AF3 GPU reconciliation audit", _version_13),
    ("workspace project icons", _version_14),
)

def _verify_current_schema(db: sqlite3.Connection) -> None:
    """Reject a core schema that lacks columns needed by current code.

    Args:
        db: Open SQLite connection after core migrations.

    Raises:
        ValueError: Any required table column is missing.
    """
    expected = {
        "agent_messages": {"parts_json"},
        "agent_runs": {"resume_attempts", "retry_after", "context_json",
                       "lease_owner", "lease_expires_at", "initial_attempts", "checkpoint_file",
                       "last_resumed_job_id", "active_resume_job_id", "turn_start_seq"},
        "agent_jobs": {"input_json", "worker_id", "lease_expires_at", "attempts",
                       "lease_token", "simulation", "first_claimed_at",
                       "resource_requirements_json", "gpu_accounting_status"},
        "agent_approvals": {"input_json"},
        "agent_token_entries": {"status"},
        "agent_af3_request_keys": {"user_id", "key", "request_hash", "job_id", "created_at"},
        "agent_model_call_guards": {"call_id", "user_id", "run_id", "prompt_bytes",
                                    "max_output_tokens", "reserved_tokens", "status", "created_at"},
        "agent_gpu_reconciliations": {"id", "job_id", "user_id", "source",
                                      "previous_minutes", "actual_minutes", "reason", "created_at"},
        "workspace_projects": {"id", "user_id", "name", "description", "archived_at", "icon"},
    }
    for table, needed in expected.items():
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if not needed <= columns:
            raise ValueError(f"Database schema is missing columns in {table}: {sorted(needed-columns)}")

def migrate_core_database(db: sqlite3.Connection, schema_sql: str) -> None:
    """Apply core schema upgrades and version stamps in one transaction.

    A failed migration rolls back both schema changes and version markers.

    Args:
        db: Open SQLite connection.
        schema_sql: Base CREATE TABLE statements for a new installation.

    Raises:
        ValueError: The database is newer than this code or lacks required columns.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("Database schema is newer than this application")
        for statement in schema_sql.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute(
            "CREATE TABLE IF NOT EXISTS core_schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL)"
        )
        if version < SCHEMA_VERSION:
            _legacy_core_columns(db)
        for target in range(version + 1, SCHEMA_VERSION + 1):
            description, migration = MIGRATIONS[target - 1]
            migration(db)
            db.execute(
                "INSERT INTO core_schema_migrations VALUES (?,?,?)",
                (target, datetime.now(UTC).isoformat(), description),
            )
            db.execute(f"PRAGMA user_version={target}")
        _verify_current_schema(db)
        if version == SCHEMA_VERSION and db.execute(
            "SELECT 1 FROM core_schema_migrations LIMIT 1"
        ).fetchone() is None:
            db.execute(
                "INSERT INTO core_schema_migrations VALUES (?,?,?)",
                (SCHEMA_VERSION, datetime.now(UTC).isoformat(), "adopted existing schema"),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise
