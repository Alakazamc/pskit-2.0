"""Real PostgreSQL admission windows, leases, and event sequence fencing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from app.contracts.conversation import MessageDeltaData, MessageDeltaEvent
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore


def seed(database, users):
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,context_json) "
            "VALUES (%s,%s,%s,'queued',%s,'{}')",
            [
                (f"{user}-{index}", user, f"session-{user}-{index}", now)
                for user, count in users
                for index in range(count)
            ],
        )


def test_thousand_full_user_candidates_use_one_bounded_native_window(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        seed(database, [("alice", 1000), ("bob", 1)])
        assert store.claim_initial_run("alice-0", "owner", max_user_active=1)
        queries = []
        original = store.db.execute_native

        def tracked(statement, parameters=()):
            result = original(statement, parameters)
            queries.append((statement, result.rowcount))
            return result

        store.db.execute_native = tracked
        assert [row[0] for row in store.claim_queued_runs("owner", max_user_active=1)] == ["bob-0"]
        windows = [count for statement, count in queries if "context_json" in statement]
        assert windows == [1]
        assert len(queries) == 3
        queries.clear()
        assert store.claim_queued_runs("owner", max_active=2) == []
        assert len(queries) == 1
    finally:
        database.close()


def test_two_instances_never_double_claim_or_exceed_user_session_limits(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    seed(database, [("alice", 100), ("bob", 100)])
    barrier = Barrier(2)

    def claim(owner):
        own = PostgresDatabase(dsn, schema=schema)
        try:
            store = PersistentConversationStore(own)
            barrier.wait(timeout=10)
            return store.claim_queued_runs(owner, max_active=4, max_user_active=2)
        finally:
            own.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            batches = list(executor.map(claim, ["first", "second"]))
        rows = [row for batch in batches for row in batch]
        # Both pools share the durable admission advisory lock.
        assert len(rows) == 4
        assert len({row[0] for row in rows}) == 4
        assert sum(row[1] == "alice" for row in rows) == 2
        assert sum(row[1] == "bob" for row in rows) == 2
    finally:
        database.close()


def test_event_batch_owner_fence_and_concurrent_sequences(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    store = PersistentConversationStore(database)
    seed(database, [("alice", 1)])
    assert store.claim_initial_run("alice-0", "owner")
    event = MessageDeltaEvent(run_id="alice-0", data=MessageDeltaData(delta="text"))
    try:
        assert store.append_events("alice", "alice-0", [event], lease_owner="wrong") is False

        def append(index):
            if index % 2:
                # Compute/approval paths append inside an existing transaction,
                # rather than through the Pi writer facade.
                with store.db:
                    store._append_event_in_transaction("alice-0", event)
                return True
            return store.append_events("alice", "alice-0", [event], lease_owner="owner")

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert all(executor.map(append, range(20)))
        events = store.events_for("alice", "alice-0", None)
        assert [item.id for item in events] == [str(index) for index in range(1, 21)]
        store.cancel_run("alice", "alice-0")
        assert store.append_events("alice", "alice-0", [event], lease_owner="owner") is False
        assert store.events_for("alice", "alice-0", None)[-1].type == "run.cancelled"
    finally:
        database.close()


def test_batch_reuses_its_run_lock_and_direct_writers_still_take_the_lock(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        seed(database, [("alice", 1)])
        assert store.claim_initial_run("alice-0", "owner")
        event = MessageDeltaEvent(run_id="alice-0", data=MessageDeltaData(delta="text"))
        locks = []
        original_execute, original_native = store.db.execute, store.db.execute_native

        def track_execute(statement, parameters=()):
            if "FOR UPDATE" in statement:
                locks.append(statement)
            return original_execute(statement, parameters)

        def track_native(statement, parameters=()):
            if "FOR UPDATE" in statement:
                locks.append(statement)
            return original_native(statement, parameters)

        store.db.execute = track_execute
        store.db.execute_native = track_native
        assert store.append_events("alice", "alice-0", [event, event], lease_owner="owner")
        assert len(locks) == 1
        locks.clear()
        with store.db:
            store._append_event_in_transaction("alice-0", event)
        assert len(locks) == 1
        assert [item.id for item in store.events_for("alice", "alice-0", None)] == ["1", "2", "3"]
    finally:
        database.close()


def test_zero_limit_prefix_has_bounded_windows_and_no_starvation(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        seed(database, [("alice", 250), ("bob", 1)])
        store.admin_concurrency_limit_for = lambda user: 0 if user == "alice" else None
        windows = []
        original = store.db.execute_native

        def tracked(statement, parameters=()):
            result = original(statement, parameters)
            if "context_json" in statement:
                windows.append(result.rowcount)
            return result

        store.db.execute_native = tracked
        claimed = []
        for _ in range(3):
            claimed.extend(row[0] for row in store.claim_queued_runs("owner"))
        assert claimed == ["bob-0"]
        assert windows == [100, 100, 51]
    finally:
        database.close()


def test_resume_claims_skip_full_user_prefix_before_loading_jobs(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        seed(database, [("alice", 1000), ("bob", 1)])
        assert store.claim_initial_run("alice-0", "owner", max_user_active=1)
        with database.transaction() as connection:
            connection.execute("UPDATE agent_runs SET status='waiting' WHERE status='queued'")
            connection.execute(
                "INSERT INTO agent_jobs (id,user_id,run_id,tool_call_id,status,progress,"
                "estimated_minutes,created_at) SELECT 'job-'||id,user_id,id,'call-'||id,"
                "'completed',100,1,created_at FROM agent_runs WHERE status='waiting'"
            )
        windows = []
        original = store.db.execute_native

        def tracked(statement, parameters=()):
            result = original(statement, parameters)
            if "JOIN agent_jobs j" in statement:
                windows.append(result.rowcount)
            return result

        store.db.execute_native = tracked
        assert [row[0] for row in store.claim_wakeups("owner", max_user_active=1)] == ["bob-0"]
        assert windows == [1]
        assert store.claim_wakeups("other", max_active=2) == []
        assert windows == [1]
    finally:
        database.close()
