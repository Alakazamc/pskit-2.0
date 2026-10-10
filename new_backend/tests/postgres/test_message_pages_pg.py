"""Real PG keyset slices materialize at most limit+1 message rows."""

from datetime import UTC, datetime

import pytest

from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.message_pages import MessageCursorUnavailable, message_page


def test_native_page_has_bounded_result_stable_ties_and_owner_cursor(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        session = store.create_session("alice", store.project_for("alice").id, "History")
        with database.transaction() as connection:
            for index in range(105):
                connection.execute(
                    "INSERT INTO agent_messages "
                    "(id,user_id,session_id,role,content,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                    (f"m-{index}", "alice", session.id, "assistant", str(index),
                     datetime(2026, 1, 1, tzinfo=UTC).isoformat()),
                )
        original = store.db.execute_native
        queries = []

        def bounded(statement, parameters=()):
            result = original(statement, parameters)
            queries.append((statement, parameters, len(result.fetchall())))
            return result

        store.db.execute_native = bounded
        page = message_page(store, "alice", session.id, limit=20)
        assert [item.id for item in page.items] == [f"m-{index}" for index in range(85, 105)]
        assert page.next_before == "m-85"
        previous = message_page(store, "alice", session.id, limit=20, before=page.next_before)
        assert [item.id for item in previous.items] == [f"m-{index}" for index in range(65, 85)]
        assert all("OFFSET" not in statement for statement, _, _ in queries)
        history_queries = [(statement, parameters, count) for statement, parameters, count in queries
                           if statement.startswith("SELECT id,role,")]
        assert len(history_queries) == 2
        assert all(statement.endswith("LIMIT %s") and parameters[-1] == 21 and count == 21
                   for statement, parameters, count in history_queries)
        assert len(store.messages_for("alice", session.id)) == 105
        assert message_page(store, "bob", session.id) is None
        other = store.create_session("alice", store.project_for("alice").id, "Other")
        for cursor in ["m-5", "missing"]:
            with pytest.raises(MessageCursorUnavailable):
                message_page(store, "alice", other.id, before=cursor)
        store.archive_session("alice", session.id)
        assert message_page(store, "alice", session.id) is None
    finally:
        database.close()
