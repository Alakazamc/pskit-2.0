"""Two independent manager stores share conservative durable sandbox activity."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.sandboxes import SandboxActivityStore, SandboxConflict


def test_two_connections_admit_only_one_attempt_per_session(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        store = SandboxActivityStore(database)
        store.ensure_owner("alice", "sandbox", "volume", "image:fixed")
        store.set_runtime("alice", "running")

        def acquire(run):
            try:
                return SandboxActivityStore(database).acquire("alice", "session", run, 60)
            except SandboxConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            leases = list(pool.map(acquire, ["run1", "run2"]))
        assert sum(lease is not None for lease in leases) == 1
        assert len(store.activity("alice").leases) == 1
    finally:
        database.close()


def test_restart_preserves_expired_unknown_until_confirmed_release(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    clock = [1000.0]
    first = PostgresDatabase(dsn, schema=schema)
    try:
        state = SandboxActivityStore(first, clock=lambda: clock[0])
        state.ensure_owner("alice", "sandbox", "volume", "image:fixed")
        state.set_runtime("alice", "running")
        lease = state.acquire("alice", "session", "attempt", 60)
    finally:
        first.close()
    clock[0] += 1801
    second = PostgresDatabase(dsn, schema=schema)
    try:
        recovered = SandboxActivityStore(second, clock=lambda: clock[0])
        assert recovered.activity("alice").state == "unknown"
        assert recovered.claim_stop("alice", 1800) is False
        with pytest.raises(SandboxConflict):
            recovered.release(lease.lease_id, lease.fencing_token + 1)
        recovered.release(lease.lease_id, lease.fencing_token)
        assert recovered.claim_stop("alice", 1800) is False
        clock[0] += 1801
        assert recovered.claim_stop("alice", 1800) is True
    finally:
        second.close()


def test_workspace_artifact_persists_with_owner_and_idempotent_digest(pg_schema):
    from app.db.postgres import PostgresStatements
    from app.domain.sandboxes import SandboxArtifactStore

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first = PostgresDatabase(dsn, schema=schema)
    try:
        artifacts = SandboxArtifactStore(PostgresStatements(first))
        ref = artifacts.put("alice", "one", "attempt", "result.txt", b"result")
        assert artifacts.put("alice", "one", "attempt", "result.txt", b"result") == ref
    finally:
        first.close()
    second = PostgresDatabase(dsn, schema=schema)
    try:
        artifacts = SandboxArtifactStore(PostgresStatements(second))
        assert artifacts.read("alice", ref.id) == ("result.txt", b"result")
        assert artifacts.read("bob", ref.id) is None
        assert artifacts.list("bob") == []
        assert len(artifacts.list("alice")) == 1
    finally:
        second.close()


def test_draining_admitted_turn_retains_separate_transfer_lease(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        state = SandboxActivityStore(database)
        state.ensure_owner("alice", "sandbox", "volume", "image:fixed")
        state.set_runtime("alice", "running")
        pi = state.acquire("alice", "one", "pi-attempt", 60)
        state.drain("alice", 0)
        transfer = state.acquire(
            "alice",
            "one",
            "transfer-attempt",
            60,
            purpose="transfer",
            continuation_run_id="pi-attempt",
        )
        assert state.owner("alice").active_sessions == ["one"]
        other = SandboxActivityStore(database)
        with pytest.raises(SandboxConflict):
            other.acquire("alice", "one", "next-pi", 60)
        with pytest.raises(SandboxConflict):
            other.acquire(
                "alice",
                "one",
                "foreign-transfer",
                60,
                purpose="transfer",
                continuation_run_id="wrong-attempt",
            )
        state.release(pi.lease_id, pi.fencing_token)
        remaining = other.activity("alice").leases
        assert len(remaining) == 1
        assert remaining[0].lease_id == transfer.lease_id
        assert remaining[0].purpose == "transfer"
        state.release(transfer.lease_id, transfer.fencing_token)
        assert state.activity("alice").state == "idle"
    finally:
        database.close()
