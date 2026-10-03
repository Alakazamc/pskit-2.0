import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import psycopg

from app.contracts.capabilities import McpInvokeResult
from app.db.migrations import migrate_mcp_tool_calls_schema
from app.db.postgres import PostgresDatabase, PostgresStatements
from app.ports.providers import ProviderUnavailable


class McpToolCallStore:
    """Prevent replay of a Pi tool call even when its first outcome is uncertain."""

    def __init__(self, path: str | PostgresDatabase) -> None:
        """Open shared SQLite storage for durable Pi MCP call identities.

        Args:
            path: Database path shared by all Pi API instances.
        """
        self._lock = Lock()
        if isinstance(path, PostgresDatabase):
            self.db = PostgresStatements(path)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        try:
            if not isinstance(path, PostgresDatabase):
                migrate_mcp_tool_calls_schema(self.db)
                self.db.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self.db.close()
            raise

    def claim(self, run_id: str, tool_call_id: str, name: str,
              arguments: dict) -> tuple[str, McpInvokeResult | None]:
        """Claim a call ID once or return its prior definitive result.

        Reusing an ID with different tool or arguments is a conflict. An
        unfinished or uncertain prior call returns ``unknown`` to avoid
        repeating external side effects.

        Args:
            run_id: Agent Run that issued the tool call.
            tool_call_id: Stable Pi or client idempotency key.
            name: Exposed MCP tool name.
            arguments: Arguments hashed in canonical JSON form.

        Returns:
            Status and optional saved result: ``claimed``, ``conflict``,
            ``completed``, or ``unknown``.

        Raises:
            ProviderUnavailable: SQLite cannot accept the claim.
        """
        arguments_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        arguments_hash = hashlib.sha256(arguments_json.encode()).hexdigest()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT name, arguments_hash, status, result_json FROM mcp_tool_calls "
                    "WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id),
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO mcp_tool_calls "
                        "(run_id,tool_call_id,name,arguments_hash,status,result_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (run_id, tool_call_id, name, arguments_hash, "running", None,
                         datetime.now(UTC).isoformat()),
                    )
                    answer = ("claimed", None)
                elif row[0] != name or row[1] != arguments_hash:
                    answer = ("conflict", None)
                elif row[2] == "completed":
                    answer = ("completed", McpInvokeResult.model_validate_json(row[3]))
                else:
                    answer = ("unknown", None)
                self.db.commit()
                return answer
            except (sqlite3.Error, psycopg.Error) as exc:
                self.db.rollback()
                raise ProviderUnavailable("MCP tool call store unavailable") from exc

    def complete(self, run_id: str, tool_call_id: str, result: McpInvokeResult) -> None:
        """Save the result of a claimed MCP call for future retries.

        Args:
            run_id: Owning Agent Run.
            tool_call_id: Claimed tool call identifier.
            result: Validated MCP result to persist.
        """
        self._set_status(run_id, tool_call_id, "completed", result.model_dump_json())

    def completed_for_run(self, run_id: str) -> list[tuple[str, str, McpInvokeResult]]:
        """Read completed MCP calls in execution order for a Run.

        Args:
            run_id: Agent Run whose results are requested.

        Returns:
            Tool call ID, name, and result tuples.

        Raises:
            ProviderUnavailable: SQLite cannot read the call ledger.
        """
        with self._lock:
            try:
                rows = self.db.execute(
                    "SELECT tool_call_id,name,result_json FROM mcp_tool_calls "
                    "WHERE run_id=? AND status='completed' ORDER BY created_at,rowid",
                    (run_id,),
                ).fetchall()
            except (sqlite3.Error, psycopg.Error) as exc:
                raise ProviderUnavailable("MCP tool call store unavailable") from exc
        return [(row[0], row[1], McpInvokeResult.model_validate_json(row[2])) for row in rows]

    def mark_unknown(self, run_id: str, tool_call_id: str) -> None:
        """Mark an unfinished call outcome unknown after a remote failure."""
        self._set_status(run_id, tool_call_id, "unknown", None)

    def release_before_invoke(self, run_id: str, tool_call_id: str) -> None:
        """Release an unstarted call so admission failure can be retried.

        Args:
            run_id: Owning Agent Run.
            tool_call_id: Call that never reached the remote server.

        Raises:
            ProviderUnavailable: SQLite cannot remove the claim.
        """
        with self._lock:
            try:
                with self.db:
                    self.db.execute(
                        "DELETE FROM mcp_tool_calls WHERE run_id=? AND tool_call_id=? AND status='running'",
                        (run_id, tool_call_id),
                    )
            except (sqlite3.Error, psycopg.Error) as exc:
                raise ProviderUnavailable("MCP tool call store unavailable") from exc

    def _set_status(self, run_id: str, tool_call_id: str, status: str, result_json: str | None) -> None:
        """Change a running call's durable state under a local lock."""
        with self._lock:
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE mcp_tool_calls SET status=?, result_json=? "
                        "WHERE run_id=? AND tool_call_id=? AND status='running'",
                        (status, result_json, run_id, tool_call_id),
                    )
            except (sqlite3.Error, psycopg.Error) as exc:
                raise ProviderUnavailable("MCP tool call store unavailable") from exc
