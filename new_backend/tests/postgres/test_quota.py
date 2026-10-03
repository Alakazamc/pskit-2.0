"""PostgreSQL Token and GPU quota concurrency contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.quota import TokenQuotaExceeded


def test_two_reservations_cannot_exceed_token_limit(pg_schema: tuple[str, str]) -> None:
    """Only one independent connection can reserve a nearly exhausted month."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    setup_database = PostgresDatabase(dsn, schema=schema)
    try:
        PersistentConversationStore(setup_database).set_token_limit("alice", 10)
    finally:
        setup_database.close()
    barrier = Barrier(2)

    def reserve(index: int) -> str:
        database = PostgresDatabase(dsn, schema=schema)
        try:
            store = PersistentConversationStore(database)
            barrier.wait(timeout=5)
            try:
                result = store.accept_message(
                    "alice", "session-alice", MessageRequest(content=f"prompt {index}"),
                    None, 8, f"key-{index}", f"hash-{index}",
                )
            except TokenQuotaExceeded:
                return "rejected"
            assert result is not None
            return "accepted"
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, (1, 2)))
    assert sorted(outcomes) == ["accepted", "rejected"]
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        assert store.usage_for("alice").tokens.used == 8
        assert len(store.messages_for("alice", "session-alice")) == 1
    finally:
        database.close()


def test_duplicate_settlement_is_once(pg_schema: tuple[str, str]) -> None:
    """A repeated model guard and release cannot double post usage."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        store.set_token_limit("alice", 100)
        accepted = store.accept_message(
            "alice", "session-alice", MessageRequest(content="analyze"),
            None, 5, "request-1", "hash-1",
        )
        assert accepted is not None
        run_id = accepted[0].run_id
        assert store.claim_initial_run(run_id, "worker-a")
        first_budget = store.reserve_model_call("alice", run_id, "call-1", 2, 5)
        assert store.reserve_model_call("alice", run_id, "call-1", 2, 5) == first_budget
        store.release_model_call("alice", run_id, "call-1")
        entries_after_first = store.usage_entries_for("alice")
        store.release_model_call("alice", run_id, "call-1")
        store.settle_current_tokens("alice", run_id)
        store.settle_current_tokens("alice", run_id)
        assert store.usage_entries_for("alice") == entries_after_first
        assert store.usage_for("alice").tokens.used == 5
    finally:
        database.close()


def test_gpu_usage_keeps_original_creation_day(pg_schema: tuple[str, str], monkeypatch) -> None:
    """A job completed tomorrow is still charged to its reservation day."""
    import app.domain.persistent_conversation as persistence

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    today = datetime(2026, 10, 3, 23, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: today)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        store.set_gpu_limit("alice", 10)
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO agent_jobs "
                "(id,user_id,status,progress,estimated_minutes,created_at) "
                "VALUES ('job-1','alice','queued',0,8,%s)",
                (today.isoformat(),),
            )
        assert store.usage_for("alice").gpu.reserved == 8
        tomorrow = today + timedelta(hours=2)
        monkeypatch.setattr(persistence, "_now", lambda: tomorrow)
        with database.transaction() as connection:
            connection.execute(
                "UPDATE agent_jobs SET status='completed',actual_minutes=6,"
                "gpu_accounting_status='settled' WHERE id='job-1'"
            )
        assert store.usage_for("alice").gpu.used == 0
        with database.connection() as connection:
            assert connection.execute(
                "SELECT actual_minutes,gpu_accounting_status FROM agent_jobs WHERE id='job-1'"
            ).fetchone() == (6, "settled")
    finally:
        database.close()


def test_model_attempt_reports_latest_guard(pg_schema: tuple[str, str]) -> None:
    """The provider usage report closes the most recently admitted call."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        store.set_token_limit("alice", 100)
        accepted = store.accept_message(
            "alice", "session-alice", MessageRequest(content="Research"),
            None, 5, "request-1", "hash-1",
        )
        assert accepted is not None
        run_id = accepted[0].run_id
        assert store.claim_initial_run(run_id, "worker-a")
        store.reserve_model_call("alice", run_id, "call-1", 2, 5)
        store.record_model_attempt("alice", run_id, 7, "completed")
        with database.connection() as connection:
            assert connection.execute(
                "SELECT status FROM agent_model_call_guards WHERE call_id='call-1'"
            ).fetchone() == ("reported",)
        assert store.usage_for("alice").tokens.used >= 7
    finally:
        database.close()
