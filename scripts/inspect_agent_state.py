#!/usr/bin/env python3
"""Inspect recent agent state for one username without printing credentials."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"pragma table_info({table})")]


def safe_row(row: sqlite3.Row) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in row.keys():
        value = row[key]
        lowered = key.lower()
        if any(marker in lowered for marker in ("password", "secret", "token")):
            result[key] = "<redacted>" if value else value
        elif isinstance(value, str) and len(value) > 800:
            result[key] = value[:800] + "..."
        else:
            result[key] = value
    return result


def recent_rows(
    connection: sqlite3.Connection,
    table: str,
    where: str,
    parameters: tuple[object, ...],
    limit: int,
) -> list[dict[str, object]]:
    table_columns = columns(connection, table)
    order_column = "created_at" if "created_at" in table_columns else "rowid"
    query = f"select * from {table} where {where} order by {order_column} desc limit ?"
    rows = connection.execute(query, (*parameters, limit)).fetchall()
    return [safe_row(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("/app/backend/data/pskit2.sqlite3"),
    )
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    user = connection.execute(
        "select id, username, role, created_at, disabled_at from users where username = ?",
        (args.username,),
    ).fetchone()
    if user is None:
        print(json.dumps({"error": "user not found"}, ensure_ascii=False))
        return 1

    user_id = user["id"]
    sessions = recent_rows(connection, "agent_sessions", "user_id = ?", (user_id,), 10)
    session_ids = [str(row["id"]) for row in sessions]
    result: dict[str, object] = {
        "schemas": {
            table: columns(connection, table)
            for table in ("agent_sessions", "agent_messages", "agent_turns")
        },
        "user": safe_row(user),
        "sessions": sessions,
        "turns": [],
        "messages": [],
        "tasks": recent_rows(connection, "tasks", "user_id = ?", (user_id,), 20),
    }
    if session_ids:
        placeholders = ",".join("?" for _ in session_ids)
        result["turns"] = recent_rows(
            connection,
            "agent_turns",
            f"session_id in ({placeholders})",
            tuple(session_ids),
            20,
        )
        result["messages"] = recent_rows(
            connection,
            "agent_messages",
            f"session_id in ({placeholders})",
            tuple(session_ids),
            30,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
