"""Bounded candidate windows shared by initial and resumed Run admission."""

import json
from collections import Counter

from app.db.postgres import PostgresStatements

CANDIDATE_WINDOW = 100


def is_qualification_context(context_json: str) -> bool:
    """Identify server-created internal Runs that ordinary workers cannot replay."""
    context = json.loads(context_json)
    return isinstance(context, dict) and context.get("purpose") == "model_qualification"


def ordinary_run_filter(store) -> str:
    """Exclude internal qualification scopes before a bounded candidate LIMIT."""
    purpose = ("r.context_json::jsonb ->> 'purpose'" if isinstance(store.db, PostgresStatements)
               else "json_extract(r.context_json, '$.purpose')")
    return f"COALESCE({purpose},'') <> 'model_qualification'"


def native_or_legacy(store, statement: str, parameters: tuple = ()):
    """Use explicit native PostgreSQL syntax for the hot admission path."""
    if isinstance(store.db, PostgresStatements):
        return store.db.execute_native(statement, parameters)
    return store.db.execute(statement.replace("%s", "?").replace("ordinal", "rowid"), parameters)


def active_counts(store, now: str) -> tuple[int, Counter]:
    """Count current leases once, before loading any candidate context."""
    rows = native_or_legacy(
        store,
        "SELECT user_id,COUNT(*) FROM agent_runs "
        "WHERE status IN ('running','resume_queued') AND lease_expires_at>%s "
        "GROUP BY user_id",
        (now,),
    ).fetchall()
    counts = Counter(dict(rows))
    return sum(counts.values()), counts


def candidate_filter(store, kind: str, counts: Counter, max_user_active: int):
    """Filter full users before LIMIT, then page past other ineligible prefixes.

    Only two cursors exist per store. They are updated under the same durable
    admission lock as claims, not keyed by arbitrary owner strings. A restart
    starts at the oldest ready candidate without changing any durable state.
    """
    full = [
        user
        for user, count in counts.items()
        if count >= store._max_active_for_user(user, max_user_active)
    ]
    clauses = [
        ordinary_run_filter(store),
        (
            "NOT EXISTS (SELECT 1 FROM agent_runs active WHERE "
            "active.session_id=r.session_id AND active.id<>r.id "
            "AND active.status IN ('running','resume_queued'))"
        )
    ]
    parameters: tuple = ()
    if full:
        clauses.append("r.user_id NOT IN (" + ",".join("%s" for _ in full) + ")")
        parameters += tuple(full)
    cursors = getattr(store, "_admission_cursors", None)
    if cursors is None:
        cursors = store._admission_cursors = {}
    cursor = cursors.get(kind)
    if cursor is not None:
        clauses.append("(r.created_at>%s OR (r.created_at=%s AND r.ordinal>%s))")
        parameters += (cursor[0], cursor[0], cursor[1])
    return " AND ".join(clauses), parameters


def advance_cursor(store, kind: str, rows: list, visited: int) -> None:
    """Continue after a full bounded window, otherwise wrap on the next poll."""
    store._admission_cursors[kind] = tuple(rows[visited - 1][-2:]) if visited else None
    if len(rows) < CANDIDATE_WINDOW and visited == len(rows):
        store._admission_cursors[kind] = None
