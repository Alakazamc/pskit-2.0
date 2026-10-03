"""AF3 PostgreSQL lease, callback, and artifact contracts."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

from app.contracts.capabilities import Af3FoldInput, ComputeWorkerResources
from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore

FOLD_INPUT = Af3FoldInput.model_validate({
    "name": "RNA complex", "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3", "version": 4,
})
WORKER_RESOURCES = ComputeWorkerResources(
    capabilities={"af3"}, gpu_count=1, gpu_memory_mb=49_152,
)


def test_parallel_claim_returns_one_lease(pg_schema: tuple[str, str]) -> None:
    """Two independent workers cannot both start the same GPU job."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    setup_database = PostgresDatabase(dsn, schema=schema)
    try:
        job = PersistentConversationStore(setup_database).create_af3_job(
            "alice", 8, fold_input=FOLD_INPUT,
        )
    finally:
        setup_database.close()
    barrier = Barrier(2)

    def claim(owner: str) -> list:
        database = PostgresDatabase(dsn, schema=schema)
        try:
            barrier.wait(timeout=5)
            return PersistentConversationStore(database).claim_compute_jobs(
                owner, WORKER_RESOURCES,
            )
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(claim, ("a6000-a", "a6000-b")))
    assert sorted(map(len, batches)) == [0, 1]
    assert [item.id for batch in batches for item in batch] == [job.id]
    assert len([item.lease_token for batch in batches for item in batch]) == 1


def test_claim_skips_row_locked_by_another_transaction(pg_schema: tuple[str, str]) -> None:
    """A busy first job must not hold up a second independent GPU claim."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        first = store.create_af3_job("alice", 5, fold_input=FOLD_INPUT)
        second = store.create_af3_job("alice", 5, fold_input=FOLD_INPUT)

        def claim_other() -> list:
            worker_database = PostgresDatabase(dsn, schema=schema)
            try:
                return PersistentConversationStore(worker_database).claim_compute_jobs(
                    "worker-b", WORKER_RESOURCES,
                )
            finally:
                worker_database.close()

        with ThreadPoolExecutor(max_workers=1) as executor, database.transaction() as blocker:
            blocker.execute("SELECT id FROM agent_jobs WHERE id=%s FOR UPDATE", (first.id,))
            batch = executor.submit(claim_other).result(timeout=2)
            assert [item.id for item in batch] == [second.id]
    finally:
        database.close()


def test_claim_only_locks_the_job_it_takes(pg_schema: tuple[str, str]) -> None:
    """A worker claiming one job leaves the next job available immediately."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    setup = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(setup)
        first = store.create_af3_job("alice", 5, fold_input=FOLD_INPUT)
        second = store.create_af3_job("alice", 5, fold_input=FOLD_INPUT)
    finally:
        setup.close()
    entered, release = Event(), Event()

    def claim_first() -> list:
        database = PostgresDatabase(dsn, schema=schema)
        try:
            worker = PersistentConversationStore(database)
            original = worker.get_af3_job

            def paused_get(user_id: str, job_id: str):
                entered.set()
                assert release.wait(timeout=5)
                return original(user_id, job_id)

            worker.get_af3_job = paused_get
            return worker.claim_compute_jobs("worker-a", WORKER_RESOURCES)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(claim_first)
        try:
            assert entered.wait(timeout=5)
            database = PostgresDatabase(dsn, schema=schema)
            try:
                other = PersistentConversationStore(database).claim_compute_jobs(
                    "worker-b", WORKER_RESOURCES,
                )
                assert [item.id for item in other] == [second.id]
            finally:
                database.close()
        finally:
            release.set()
        assert [item.id for item in future.result(timeout=5)] == [first.id]


def test_replayed_callback_does_not_duplicate_artifact_or_gpu_charge(
    pg_schema: tuple[str, str], monkeypatch,
) -> None:
    """The worker may retry a successful callback after losing its HTTP reply."""
    import app.domain.persistent_conversation as persistence

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    today = datetime(2026, 10, 3, 23, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: today)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        store.set_gpu_limit("alice", 10)
        job = store.create_af3_job("alice", 8, fold_input=FOLD_INPUT)
        claim = store.claim_compute_jobs("a6000", WORKER_RESOURCES)[0]
        artifact = store.save_artifact_blob(
            job.id, "artifact-1", "result.cif", "structure", b"data\x00\xff",
            attempt=claim.attempt, lease_token=claim.lease_token,
        )
        assert artifact is not None
        tomorrow = today + timedelta(hours=2)
        monkeypatch.setattr(persistence, "_now", lambda: tomorrow)
        metadata = [{"id": artifact.id, "name": artifact.name, "kind": artifact.kind}]
        first = store.settle_af3_job(
            job.id, "completed", 6, metadata,
            attempt=claim.attempt, lease_token=claim.lease_token,
        )
        second = store.settle_af3_job(
            job.id, "completed", 6, metadata,
            attempt=claim.attempt, lease_token=claim.lease_token,
        )
        assert first == second
        assert store.usage_for("alice").gpu.used == 0
        assert store._gpu_usage_values("alice", today.date().isoformat()) == (6, 0)
        with database.connection() as connection:
            assert connection.execute(
                "SELECT count(*) FROM agent_artifact_blobs WHERE job_id=%s", (job.id,)
            ).fetchone() == (1,)
    finally:
        database.close()


def test_artifact_round_trip_preserves_sha256(pg_schema: tuple[str, str]) -> None:
    """Artifact bytes stay private and repeat uploads cannot change their digest."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        job = store.create_af3_job("alice", 5, fold_input=FOLD_INPUT)
        claim = store.claim_compute_jobs("a6000", WORKER_RESOURCES)[0]
        with pytest.raises(ValueError, match="20 MiB"):
            store.save_artifact_blob(
                job.id, "artifact-large", "huge.cif", "structure",
                b"x" * (20 * 1024 * 1024 + 1),
                attempt=claim.attempt, lease_token=claim.lease_token,
            )
        payload = b"data\x00\xff\n"
        artifact = store.save_artifact_blob(
            job.id, "artifact-1", "result.cif", "structure", payload,
            attempt=claim.attempt, lease_token=claim.lease_token,
        )
        assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
        assert store.save_artifact_blob(
            job.id, "artifact-1", "result.cif", "structure", payload,
            attempt=claim.attempt, lease_token=claim.lease_token,
        ) == artifact
        with pytest.raises(ValueError):
            store.save_artifact_blob(
                job.id, "artifact-1", "result.cif", "structure", b"changed",
                attempt=claim.attempt, lease_token=claim.lease_token,
            )
        store.settle_af3_job(
            job.id, "completed", 4,
            [{"id": artifact.id, "name": artifact.name, "kind": artifact.kind}],
            attempt=claim.attempt, lease_token=claim.lease_token,
        )
        assert store.artifact_bytes_for("alice", artifact.id) == ("result.cif", payload)
        assert store.artifact_bytes_for("bob", artifact.id) is None
    finally:
        database.close()


def test_approval_creates_one_job_and_keeps_user_ownership(pg_schema: tuple[str, str]) -> None:
    """Repeated approval decisions never reserve GPU twice or expose another user's job."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        run = store.send_message("alice", "session-alice", MessageRequest(content="Run AF3"))
        assert run is not None and store.claim_initial_run(run.run_id, "agent-a")
        approval = store.request_af3_approval("alice", run.run_id, "call-1", 8, FOLD_INPUT)
        store.set_run_status(run.run_id, "waiting")
        first = store.decide_af3_approval("alice", run.run_id, approval.approval_id, "approved")
        second = store.decide_af3_approval("alice", run.run_id, approval.approval_id, "approved")
        assert first == second and first.job_id is not None
        assert store.usage_for("alice").gpu.reserved == 8
        assert store.get_af3_job("bob", first.job_id) is None
        with database.connection() as connection:
            assert connection.execute(
                "SELECT count(*) FROM agent_jobs WHERE run_id=%s AND tool_call_id='call-1'",
                (run.run_id,),
            ).fetchone() == (1,)
    finally:
        database.close()


def test_cancelled_job_reconciles_late_gpu_report_once_across_days(
    pg_schema: tuple[str, str], monkeypatch,
) -> None:
    """A cancellation keeps the GPU hold until a fenced worker reports usage."""
    import app.domain.persistent_conversation as persistence

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    today = datetime(2026, 10, 3, 23, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: today)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = PersistentConversationStore(database)
        store.set_gpu_limit("alice", 10)
        job = store.create_af3_job("alice", 8, fold_input=FOLD_INPUT)
        claim = store.claim_compute_jobs("a6000", WORKER_RESOURCES)[0]
        cancelled = store.cancel_af3_job("alice", job.id)
        assert cancelled.gpu_accounting_status == "pending_reconciliation"
        assert store.usage_for("alice").gpu.reserved == 8
        monkeypatch.setattr(persistence, "_now", lambda: today + timedelta(hours=2))
        first = store.settle_af3_job(
            job.id, "completed", 6, [], attempt=claim.attempt, lease_token=claim.lease_token,
        )
        second = store.settle_af3_job(
            job.id, "completed", 6, [], attempt=claim.attempt, lease_token=claim.lease_token,
        )
        assert first == second and first.gpu_accounting_status == "reconciled"
        assert len(store.gpu_reconciliations_for(job.id)) == 1
        assert store._gpu_usage_values("alice", today.date().isoformat()) == (6, 0)
        assert store.usage_for("alice").gpu.used == 0
    finally:
        database.close()
