"""Bounded keyset history reads alongside the unchanged transcript API.

The entire repository read can cross one async boundary. PostgreSQL SQL uses
native placeholders/ordinal; the SQLite branch only supports legacy fixtures.
"""

from datetime import datetime

from app.contracts.conversation import Message, MessagePage, TextPart
from app.domain.persistent_conversation.common import MESSAGE_PARTS_ADAPTER


class MessageCursorUnavailable(ValueError):
    """Unknown and foreign cursors are indistinguishable to the caller."""


def message_page(store, user_id: str, session_id: str, *, limit: int = 50,
                 before: str | None = None) -> MessagePage | None:
    """Return newest bounded messages in chronological order without OFFSET."""
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Invalid history page size")
    if before is not None and not 1 <= len(before) <= 200:
        raise MessageCursorUnavailable("Message cursor unavailable")
    if not hasattr(store, "db"):
        rows = store.messages_for(user_id, session_id)
        if rows is None:
            return None
        end = len(rows)
        if before is not None:
            end = next((index for index, row in enumerate(rows) if row.id == before), -1)
            if end < 0:
                raise MessageCursorUnavailable("Message cursor unavailable")
        items = list(rows[max(0, end - limit):end])
        return MessagePage(items=items, next_before=items[0].id if end > limit else None)
    database = getattr(store, "database", None)
    execute = store.db.execute_native if database is not None else store.db.execute
    placeholder, ordinal = ("%s", "ordinal") if database is not None else ("?", "rowid")
    with store.db:
        if not store._owns_session(user_id, session_id):
            return None
        parameters = [user_id, session_id]
        predicate = ""
        if before is not None:
            cursor = execute(
                f"SELECT created_at,{ordinal} FROM agent_messages "
                f"WHERE user_id={placeholder} AND session_id={placeholder} AND id={placeholder}",
                (user_id, session_id, before),
            ).fetchone()
            if cursor is None:
                raise MessageCursorUnavailable("Message cursor unavailable")
            predicate = f" AND (created_at,{ordinal}) < ({placeholder},{placeholder})"
            parameters.extend(cursor)
        rows = execute(
            "SELECT id,role,content,created_at,parts_json FROM agent_messages "
            f"WHERE user_id={placeholder} AND session_id={placeholder}{predicate} "
            f"ORDER BY created_at DESC,{ordinal} DESC LIMIT {placeholder}",
            (*parameters, limit + 1),
        ).fetchall()
        items = [Message(
            id=row[0], session_id=session_id, role=row[1],
            parts=(MESSAGE_PARTS_ADAPTER.validate_json(row[4]) if row[4]
                   else [TextPart(text=row[2])]),
            created_at=datetime.fromisoformat(row[3]),
        ) for row in reversed(rows[:limit])]
        return MessagePage(items=items,
                           next_before=items[0].id if len(rows) > limit else None)
