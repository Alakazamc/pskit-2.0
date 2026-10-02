import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.db.migrations import migrate_tool_run_schema


class ToolRun(BaseModel):
    id: str
    tool: str
    title: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime
    project_id: str | None = None


class ToolRunStore:
    """Store tool history in SQLite or an isolated in-memory mock."""

    def __init__(self, db_path: str | None = None) -> None:
        """Open persistent history when a database path is supplied.

        Args:
            db_path: Optional SQLite path; absence selects the mock store.
        """
        self._runs: dict[str, dict[str, ToolRun]] = {}
        self.db = sqlite3.connect(db_path, check_same_thread=False) if db_path else None
        if self.db:
            try:
                migrate_tool_run_schema(self.db)
                self.db.execute("PRAGMA journal_mode=WAL")
            except BaseException:
                self.db.close()
                raise

    def add(self, user_id: str, tool: str, arguments: dict, result: dict) -> ToolRun:
        """Append a completed tool call under its user ID.

        Args:
            user_id: Owner of the tool record.
            tool: Registered tool name.
            arguments: Arguments sent to the tool.
            result: Returned result payload.

        Returns:
            Saved tool execution record.
        """
        query = arguments.get("query") or arguments.get("accession")
        title = f"{tool}: {query}" if isinstance(query, str) and query else tool
        run = ToolRun(id=f"toolrun-{uuid.uuid4()}", tool=tool, title=title,
                      arguments=arguments, result=result, created_at=datetime.now(UTC))
        if self.db:
            with self.db:
                self.db.execute(
                    "INSERT INTO tool_runs VALUES (?,?,?,?,?,?,?,NULL)",
                    (run.id, user_id, run.tool, run.title, json.dumps(arguments),
                     json.dumps(result), run.created_at.isoformat()),
                )
        else:
            self._runs.setdefault(user_id, {})[run.id] = run
        return run

    def list_for(self, user_id: str) -> list[ToolRun]:
        """List a user's tool records from newest to oldest.

        Args:
            user_id: Owner whose history is requested.

        Returns:
            Ordered tool execution records.
        """
        if self.db:
            rows = self.db.execute(
                "SELECT id,tool,title,arguments_json,result_json,created_at,project_id "
                "FROM tool_runs WHERE user_id=? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
            return [ToolRun(id=row[0], tool=row[1], title=row[2],
                            arguments=json.loads(row[3]), result=json.loads(row[4]),
                            created_at=datetime.fromisoformat(row[5]), project_id=row[6]) for row in rows]
        return sorted(self._runs.get(user_id, {}).values(), key=lambda run: run.created_at, reverse=True)

    def set_project(self, user_id: str, run_id: str, project_id: str) -> ToolRun | None:
        """Associate an owned tool record with a project.

        Args:
            user_id: Tool record owner.
            run_id: Tool record to update.
            project_id: Destination project ID.

        Returns:
            Updated record, or ``None`` when unavailable.
        """
        if self.db:
            with self.db:
                changed = self.db.execute(
                    "UPDATE tool_runs SET project_id=? WHERE id=? AND user_id=?",
                    (project_id, run_id, user_id),
                ).rowcount
            return next((run for run in self.list_for(user_id) if run.id == run_id), None) if changed else None
        run = self._runs.get(user_id, {}).get(run_id)
        if run is None:
            return None
        updated = run.model_copy(update={"project_id": project_id})
        self._runs[user_id][run_id] = updated
        return updated
