"""Components SQLite schema migrations."""

import sqlite3
from datetime import UTC, datetime

from .common import (
    call_add_column_if_missing as add_column_if_missing,
)
from .common import (
    migrate_component_database,
)


def migrate_identity_policy_schema(db: sqlite3.Connection) -> None:
    """Add member and guest identity tables, including cleanup state.

    Existing user IDs in workspace and run tables are backfilled as members.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    def create_account_tiers(connection: sqlite3.Connection) -> None:
        """Create account tiers and classify preexisting users as members."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS account_tiers ("
            "user_id TEXT PRIMARY KEY, tier TEXT NOT NULL CHECK(tier IN ('guest','member')), "
            "last_seen_at TEXT NOT NULL)"
        )
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        seen_at = datetime.now(UTC).isoformat()
        for table in (
            "workspace_projects", "workspace_sessions", "agent_runs", "agent_jobs",
            "agent_messages", "pi_sessions", "catalog_files", "tool_runs",
        ):
            if table in existing:
                connection.execute(
                    f"INSERT OR IGNORE INTO account_tiers (user_id,tier,last_seen_at) "
                    f"SELECT DISTINCT user_id,'member',? FROM {table} "
                    "WHERE user_id IS NOT NULL AND user_id != ''",
                    (seen_at,),
                )

    def create_guest_email_upgrades(connection: sqlite3.Connection) -> None:
        """Create pending guest email upgrade records."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS guest_email_upgrades ("
            "user_id TEXT PRIMARY KEY, email TEXT NOT NULL, started_at TEXT NOT NULL)"
        )

    def add_cleanup_state(connection: sqlite3.Connection) -> None:
        """Add guest deletion state, claims, and audit tables."""
        add_column_if_missing(
            connection, "account_tiers", "cleanup_state",
            "TEXT NOT NULL DEFAULT 'active'",
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS guest_cleanup_claims ("
            "user_id TEXT PRIMARY KEY, state TEXT NOT NULL, claimed_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS guest_cleanup_audit ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_hash TEXT NOT NULL, "
            "outcome TEXT NOT NULL, created_at TEXT NOT NULL)"
        )

    migrate_component_database(db, "identity_policy", (
        ("persistent account tiers", create_account_tiers),
        ("pending guest email upgrades", create_guest_email_upgrades),
        ("anonymous identity cleanup state", add_cleanup_state),
    ), {
        "account_tiers": {"user_id", "tier", "last_seen_at", "cleanup_state"},
        "guest_email_upgrades": {"user_id", "email", "started_at"},
        "guest_cleanup_claims": {"user_id", "state", "claimed_at"},
        "guest_cleanup_audit": {"id", "user_hash", "outcome", "created_at"},
    })

def _catalog_base(db: sqlite3.Connection) -> None:
    """Create uploaded file and Skill grant tables."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS catalog_files ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, "
        "size INTEGER NOT NULL, content TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS catalog_skill_grants ("
        "user_id TEXT PRIMARY KEY, skill_ids_json TEXT NOT NULL)"
    )

def _catalog_raw_content(db: sqlite3.Connection) -> None:
    """Keep original uploaded bytes alongside parsed content."""
    add_column_if_missing(db, "catalog_files", "raw_content", "BLOB")

def _catalog_skill_versions(db: sqlite3.Connection) -> None:
    """Create versioned Skill definitions."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS catalog_skill_versions ("
        "id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, "
        "description TEXT NOT NULL, tools_json TEXT NOT NULL, instructions TEXT NOT NULL, "
        "created_at TEXT NOT NULL, PRIMARY KEY(id,version))"
    )

def migrate_catalog_schema(db: sqlite3.Connection) -> None:
    """Upgrade file and Skill catalog tables in one component transaction.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "catalog", (
        ("catalog files and skill grants", _catalog_base),
        ("raw upload bytes", _catalog_raw_content),
        ("registered Skill versions", _catalog_skill_versions),
    ), {
        "catalog_files": {"id", "user_id", "name", "size", "content", "raw_content"},
        "catalog_skill_grants": {"user_id", "skill_ids_json"},
        "catalog_skill_versions": {
            "id", "version", "name", "description", "tools_json", "instructions", "created_at",
        },
    })

def _oauth_base(db: sqlite3.Connection) -> None:
    """Create durable OAuth state and PKCE verifier storage."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS oauth_flows ("
        "state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires_at REAL NOT NULL)"
    )

def migrate_oauth_schema(db: sqlite3.Connection) -> None:
    """Upgrade OAuth state to track guest identity linking.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    def add_guest_flow_owner(connection: sqlite3.Connection) -> None:
        """Associate OAuth state with the initiating guest user."""
        add_column_if_missing(connection, "oauth_flows", "guest_user_id", "TEXT")

    migrate_component_database(db, "oauth", (
        ("durable OAuth state", _oauth_base),
        ("guest identity linking state", add_guest_flow_owner),
    ), {
        "oauth_flows": {"state", "verifier", "expires_at", "guest_user_id"},
    })

def _tool_runs_base(db: sqlite3.Connection) -> None:
    """Create persistent tool execution history."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS tool_runs ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tool TEXT NOT NULL, "
        "title TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL, project_id TEXT)"
    )

def migrate_tool_run_schema(db: sqlite3.Connection) -> None:
    """Create and verify the tool execution history table.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "tool_runs", (("tool execution history", _tool_runs_base),), {
        "tool_runs": {"id", "user_id", "tool", "title", "arguments_json",
                      "result_json", "created_at", "project_id"},
    })

def _mcp_capacity_base(db: sqlite3.Connection) -> None:
    """Create shared MCP concurrency configuration and lease tables."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS mcp_capacity_config ("
        "scope TEXT PRIMARY KEY, max_calls INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS mcp_execution_leases ("
        "token TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
    )

def migrate_mcp_capacity_schema(db: sqlite3.Connection) -> None:
    """Create and verify shared MCP execution capacity tables.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "mcp_capacity", (("shared MCP execution leases", _mcp_capacity_base),), {
        "mcp_capacity_config": {"scope", "max_calls"},
        "mcp_execution_leases": {"token", "expires_at"},
    })

def _pdf_capacity_base(db: sqlite3.Connection) -> None:
    """Create shared PDF parsing concurrency and lease tables."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS pdf_capacity_config ("
        "scope TEXT PRIMARY KEY, max_calls INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS pdf_execution_leases ("
        "token TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
    )

def migrate_pdf_capacity_schema(db: sqlite3.Connection) -> None:
    """Create and verify shared PDF parsing capacity tables.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "pdf_capacity", (("shared PDF parsing leases", _pdf_capacity_base),), {
        "pdf_capacity_config": {"scope", "max_calls"},
        "pdf_execution_leases": {"token", "expires_at"},
    })

def _mcp_tool_calls_base(db: sqlite3.Connection) -> None:
    """Persist Pi MCP call identity, arguments hash, and result state."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS mcp_tool_calls ("
        "run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, name TEXT NOT NULL, "
        "arguments_hash TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, "
        "created_at TEXT NOT NULL, PRIMARY KEY(run_id, tool_call_id))"
    )

def migrate_mcp_tool_calls_schema(db: sqlite3.Connection) -> None:
    """Create and verify durable Pi MCP tool call records.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "mcp_tool_calls", (("durable Pi MCP tool calls", _mcp_tool_calls_base),), {
        "mcp_tool_calls": {"run_id", "tool_call_id", "name", "arguments_hash", "status", "result_json", "created_at"},
    })

def _internal_tool_auth_base(db: sqlite3.Connection) -> None:
    """Create storage for the shared Pi internal tool key."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS internal_tool_auth ("
        "id TEXT PRIMARY KEY, secret BLOB NOT NULL)"
    )

def migrate_internal_tool_auth_schema(db: sqlite3.Connection) -> None:
    """Create and verify shared Pi internal tool authentication storage.

    Args:
        db: Open SQLite connection shared with the core schema.
    """
    migrate_component_database(db, "internal_tool_auth", (("shared Pi internal tool key", _internal_tool_auth_base),), {
        "internal_tool_auth": {"id", "secret"},
    })
