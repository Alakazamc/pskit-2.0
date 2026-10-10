"""Additive bounded history reads preserve the original transcript contract."""

from datetime import UTC, datetime

import pytest

from app.contracts.conversation import ArtifactPart, Message, TextPart
from app.domain.conversation import ConversationStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.message_pages import MessageCursorUnavailable, message_page


@pytest.fixture(params=["mock", "sqlite"])
def history(request, tmp_path):
    store = (ConversationStore() if request.param == "mock" else
             PersistentConversationStore(str(tmp_path / "history.db")))
    project = store.project_for("alice")
    session = store.create_session("alice", project.id, "History")
    rows = [Message(id=f"message-{index}", session_id=session.id, role="assistant",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    parts=[TextPart(text=str(index)), ArtifactPart(
                        id=f"artifact-{index}", name="report.md", kind="markdown")])
            for index in range(10)]
    if request.param == "mock":
        store.messages_for("alice", session.id).extend(rows)
    else:
        from app.domain.persistent_conversation.common import MESSAGE_PARTS_ADAPTER
        with store.db:
            store.db.executemany(
                "INSERT INTO agent_messages "
                "(id,user_id,session_id,role,content,created_at,parts_json) VALUES (?,?,?,?,?,?,?)",
                [(row.id, "alice", session.id, row.role, str(index), row.created_at.isoformat(),
                  MESSAGE_PARTS_ADAPTER.dump_json(row.parts).decode())
                 for index, row in enumerate(rows)],
            )
    yield store, session.id, rows
    if request.param == "sqlite":
        store.db.close()


def test_pages_are_latest_first_request_but_chronological_with_stable_ties(history):
    store, session, rows = history
    first = message_page(store, "alice", session, limit=4)
    assert first.items == rows[6:]
    assert first.next_before == rows[6].id
    second = message_page(store, "alice", session, limit=4, before=first.next_before)
    assert second.items == rows[2:6]
    third = message_page(store, "alice", session, limit=4, before=second.next_before)
    assert third.items == rows[:2]
    assert third.next_before is None
    assert store.messages_for("alice", session) == rows


def test_owner_archived_and_foreign_cursor_do_not_leak_history(history):
    store, session, _ = history
    assert message_page(store, "bob", session) is None
    other = store.create_session("alice", store.project_for("alice").id, "Other")
    with pytest.raises(MessageCursorUnavailable):
        message_page(store, "alice", other.id, before="message-4")
    with pytest.raises(MessageCursorUnavailable):
        message_page(store, "alice", session, before="absent")
    store.archive_session("alice", session)
    assert message_page(store, "alice", session) is None


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_page_bound_rejected_before_query(history, limit):
    store, session, _ = history
    with pytest.raises(ValueError):
        message_page(store, "alice", session, limit=limit)
