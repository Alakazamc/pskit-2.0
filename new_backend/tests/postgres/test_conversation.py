"""PostgreSQL conversation, Run, and recovery contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from app.contracts.conversation import (
    MessageDeltaData,
    MessageDeltaEvent,
    MessageRequest,
    TextPart,
    ToolStartedData,
    ToolStartedEvent,
)
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore


def test_workspace_and_run_survive_reopen(pg_schema: tuple[str, str]) -> None:
    """Project, typed messages, event cursor, and Pi path survive pool restart."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first_database = PostgresDatabase(dsn, schema=schema)
    first = PersistentConversationStore(first_database)
    project = first.create_project("alice", "RNA Design", "Analysis")
    session = first.create_session("alice", project.id, "First experiment")
    assert session is not None
    run = first.send_message("alice", session.id, MessageRequest(content="Analyze RNA"))
    assert run is not None
    first.append_event("alice", run.run_id, MessageDeltaEvent(
        run_id=run.run_id, data=MessageDeltaData(delta="working"),
    ))
    first.set_session_file("alice", session.id, "/data/pi-sessions/existing.jsonl")
    first_database.close()

    second_database = PostgresDatabase(dsn, schema=schema)
    try:
        second = PersistentConversationStore(second_database)
        assert project in second.projects_for("alice")
        sessions = second.sessions_for("alice", project.id)
        assert [item.id for item in sessions] == [session.id]
        assert sessions[0].latest_run_id == run.run_id
        assert [message.parts for message in second.messages_for("alice", session.id)] == [
            [TextPart(text="Analyze RNA")],
        ]
        assert [event.data.delta for event in second.events_for("alice", run.run_id, None)] == [
            "working",
        ]
        assert second.session_file_for("alice", session.id) == "/data/pi-sessions/existing.jsonl"
        assert second.messages_for("bob", session.id) is None
    finally:
        second_database.close()


def test_two_resumers_claim_one_run(pg_schema: tuple[str, str]) -> None:
    """Independent connections cannot lease the same completed background Run twice."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        run = store.send_message("alice", "session-alice", MessageRequest(content="Run AF3"))
        assert run is not None
        with database.transaction() as connection:
            connection.execute("UPDATE agent_runs SET status='waiting' WHERE id=%s", (run.run_id,))
            connection.execute(
                "INSERT INTO agent_jobs "
                "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,created_at) "
                "VALUES ('job-1','alice',%s,'call-1','completed',100,1,'now')",
                (run.run_id,),
            )

        barrier = Barrier(2)

        def claim(owner: str) -> list[tuple[str, str, str, str]]:
            worker_database = PostgresDatabase(dsn, schema=schema)
            try:
                worker = PersistentConversationStore(worker_database)
                barrier.wait(timeout=5)
                return worker.claim_wakeups(owner)
            finally:
                worker_database.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("worker-a", "worker-b")))
        assert sorted(map(len, results)) == [0, 1]
        assert [row[0] for batch in results for row in batch] == [run.run_id]
        assert store.events_for("alice", run.run_id, None) == []
    finally:
        database.close()


def test_pi_turn_checkpoint_and_workspace_changes_are_atomic(pg_schema: tuple[str, str]) -> None:
    """Skill order, session movement, and final Pi transcript persist together."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        project = store.create_project("alice", "Proteins", "Structures")
        session = store.create_session("alice", project.id, "Prediction")
        assert session is not None
        assert store.rename_project("alice", project.id, "RNA") is not None
        assert store.set_project_icon("alice", project.id, "dna") is not None
        moved = store.move_session("alice", session.id, store.project_for("alice").id)
        assert moved is not None and moved.project_id == store.project_for("alice").id
        run = store.send_message("alice", session.id, MessageRequest(content="Analyze"))
        assert run is not None
        assert store.claim_initial_run(run.run_id, "worker-1")
        assert store.start_pi_turn(run.run_id, "worker-1")
        assert store.finish_pi_turn(
            "alice", session.id, run.run_id, "worker-1", "/data/pi-sessions/run.jsonl",
            "Done", waiting=False,
        )
        assert store.run_status_for("alice", run.run_id).status == "completed"
        assert [part.text for message in store.messages_for("alice", session.id)
                for part in message.parts if isinstance(part, TextPart)] == ["Analyze", "Done"]
        assert store.finish_pi_turn(
            "alice", session.id, run.run_id, "worker-1", "/data/pi-sessions/late.jsonl",
            "Late", waiting=False,
        ) is False
        assert store.session_file_for("alice", session.id) == "/data/pi-sessions/run.jsonl"
    finally:
        database.close()


def test_interrupted_postgres_run_with_started_tool_fails_closed(
    pg_schema: tuple[str, str], monkeypatch,
) -> None:
    """A stale Pi lease cannot replay a tool that already started."""
    import app.domain.persistent_conversation as persistence

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        run = store.send_message("alice", "session-alice", MessageRequest(content="Analyze"))
        assert run is not None and store.claim_initial_run(run.run_id, "worker-a")
        store.append_event("alice", run.run_id, ToolStartedEvent(
            run_id=run.run_id,
            data=ToolStartedData(tool_call_id="call-1", tool="external_write"),
        ))
        now += timedelta(seconds=11)
        store.recover_wakeups(retry_seconds=0.01)
        assert store.run_status_for("alice", run.run_id).status == "failed"
        assert [event.type for event in store.events_for("alice", run.run_id, None)] == [
            "tool.started", "run.failed",
        ]
    finally:
        database.close()
