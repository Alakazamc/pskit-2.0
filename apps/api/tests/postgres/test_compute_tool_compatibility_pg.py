"""Run-scoped compute compatibility survives PostgreSQL restarts and retries."""

import pytest

from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent import AgentService


@pytest.mark.parametrize("enabled", [True, False])
def test_restart_and_idempotent_retry_preserve_new_run_policy_without_rewriting_history(pg_schema, enabled):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    store = PersistentConversationStore(database)
    payload = MessageRequest(content="Synthetic document processing")
    try:
        old = store.send_message("alice", "session-alice", payload)
        run, created = store.accept_message(
            "alice", "session-alice", payload, None, 1, "compute-same", "hash",
            compute_tool_compatibility=enabled,
        )
        assert created
        assert "compute_tool_compatibility" not in store.run_context(old.run_id)
    finally:
        database.close()

    reopened = PostgresDatabase(dsn, schema=schema)
    restored = PersistentConversationStore(reopened)
    try:
        duplicate, created = restored.accept_message(
            "alice", "session-alice", payload, None, 1, "compute-same", "hash",
            compute_tool_compatibility=not enabled,
        )
        assert not created and duplicate == run
        assert restored.run_context(run.run_id)["compute_tool_compatibility"] is enabled
        agent = AgentService(restored, None, "http://internal", 0, 1)
        assert agent._environment("alice", run.run_id)["PSKIT_COMPUTE_COMPAT_ENABLED"] == (
            "1" if enabled else "0"
        )
        assert agent._environment("alice", old.run_id)["PSKIT_COMPUTE_COMPAT_ENABLED"] == "1"
        assert "compute_tool_compatibility" not in restored.run_context(old.run_id)
        assert restored.db.execute(
            "SELECT COUNT(*) FROM agent_token_entries WHERE run_id=? AND kind='reservation'",
            (run.run_id,),
        ).fetchone()[0] == 1
    finally:
        reopened.close()
