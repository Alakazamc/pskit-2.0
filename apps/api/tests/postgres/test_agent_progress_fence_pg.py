"""Real PG first/buffered text commits reject lost leases atomically."""

from datetime import UTC, datetime, timedelta

import pytest

from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent import AgentService
from app.services.agent_events import AgentProgressWriter


def text_event(text):
    return {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}}


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["cancelled", "expired", "new-owner"])
async def test_buffered_text_and_late_first_frame_require_a_current_lease(pg_schema, loss):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        run = store.send_message("alice", "session-alice", MessageRequest(content="synthetic"))
        assert store.claim_initial_run(run.run_id, "worker")
        service = object.__new__(AgentService)
        service.store, service._instance_id = store, "worker"
        results = []
        append = store.append_events

        def observed(*args, **kwargs):
            result = append(*args, **kwargs)
            results.append(result)
            return result

        store.append_events = observed
        writer = AgentProgressWriter(service, "alice", run.run_id)
        await writer.on_event(text_event("durable"))
        await writer.on_event(text_event("buffered"))
        if loss == "cancelled":
            await service._db_call(store.cancel_run, "alice", run.run_id)
        else:
            with database.transaction() as connection:
                if loss == "expired":
                    connection.execute(
                        "UPDATE agent_runs SET lease_expires_at=%s WHERE id=%s",
                        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), run.run_id),
                    )
                else:
                    connection.execute("UPDATE agent_runs SET lease_owner='other' WHERE id=%s", (run.run_id,))
        await writer.close()
        late_writer = AgentProgressWriter(service, "alice", run.run_id)
        await late_writer.on_event(text_event("late-first"))
        await late_writer.close()
        assert results == [True, False, False]
        events = store.events_for("alice", run.run_id, None)
        assert [event.data.delta for event in events if event.type == "message.delta"] == ["durable"]
    finally:
        database.close()


@pytest.mark.asyncio
async def test_text_commit_also_rejects_a_foreign_user_with_the_same_worker(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        run = store.send_message("alice", "session-alice", MessageRequest(content="synthetic"))
        assert store.claim_initial_run(run.run_id, "worker")
        service = object.__new__(AgentService)
        service.store, service._instance_id = store, "worker"
        writer = AgentProgressWriter(service, "bob", run.run_id)
        with pytest.raises(ValueError, match="owner mismatch"):
            await writer.on_event(text_event("foreign"))
        await writer.close()
        assert store.events_for("alice", run.run_id, None) == []
    finally:
        database.close()
