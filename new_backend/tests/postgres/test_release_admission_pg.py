"""Real PostgreSQL gates hold through admissions, quotas, claims and restarts."""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from compute_support import request
from psycopg.conninfo import make_conninfo

from app.adapters.mock.mcp import MockMcp
from app.api.auth import get_current_user
from app.config import Settings
from app.contracts.capabilities import ComputeWorkerResources
from app.contracts.compute import ComputeClaimRequest, WorkerResources
from app.contracts.conversation import MessageRequest
from app.contracts.models import UserIdentity
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import SCHEMA_VERSION, migrate_postgres
from app.db.release_admission_migrations import migrate_admission_postgres
from app.domain.compute.leases import ComputeLeases
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.release_admission import AdmissionClosed, AdmissionUnavailable, DurableAdmissionGate
from app.domain.workspace_attempts import WorkspaceAttemptStore
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.main import create_app
from app.ports.workspace_sandbox import (
    WorkspaceCapabilities,
    WorkspaceCommandHandle,
    WorkspaceSandbox,
)
from app.services.workspace_commands import WorkspaceCommands
from app.services.workspace_files import WorkspaceOperationContext


def identity():
    return {"adapter_id": "synthetic-adapter", "qualification_sha256": "a" * 64,
            "owner_nonce": "b" * 64, "environment": "staging", "project": "pskit-agent-staging",
            "previous_manifest_sha256": "c" * 64, "target_manifest_sha256": "d" * 64,
            "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"]}


def make_gate(database):
    migrate_admission_postgres(database.pool.conninfo, schema=database.schema)
    gate = DurableAdmissionGate(database, environment="staging", project="pskit-agent-staging",
                                qualification_sha256="a" * 64)
    gate.configure()
    return gate


@pytest.fixture
def system(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        gate = make_gate(database)
        yield database, gate, PersistentConversationStore(database, admission_gate=gate)
    finally:
        database.close()


def test_enter_ack_waits_for_prior_full_admission_transaction(system, pg_schema):
    _database, gate, store = system
    dsn, schema = pg_schema
    session = store.create_session("alice", store.project_for("alice").id, "Synthetic")
    admitted, commit = threading.Event(), threading.Event()
    checked = gate.require_open

    def pause_after_gate(connection):
        checked(connection)
        admitted.set()
        assert commit.wait(timeout=5)

    gate.require_open = pause_after_gate
    application = "admission-enter-" + uuid.uuid4().hex
    independent = PostgresDatabase(make_conninfo(dsn, application_name=application), schema=schema)
    closer = make_gate(independent)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(store.accept_message, "alice", session.id,
                                   MessageRequest(content="before hold"), None, 100, "old", "old")
            try:
                assert admitted.wait(timeout=2)
                entering = pool.submit(closer.enter, identity())
                deadline = time.monotonic() + 2
                waiting = False
                with psycopg.connect(dsn, autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        waiting = observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                            "AND wait_event='advisory')", (application,),
                        ).fetchone()[0]
                        if waiting:
                            break
                        time.sleep(0.01)
                assert waiting, "Enter must wait on the whole preceding admission transaction"
                assert not entering.done()
            finally:
                commit.set()
            response, created = accepted.result(timeout=3)
            assert created
            assert entering.result(timeout=3)["state"] == "closed"
        gate.require_open = checked
        entries = store.db.execute("SELECT * FROM agent_token_entries ORDER BY id").fetchall()
        with ThreadPoolExecutor(max_workers=6) as pool:
            def rejected(index):
                with pytest.raises(AdmissionClosed):
                    store.accept_message("alice", session.id, MessageRequest(content="new"), None,
                                         200, str(index), str(index))
            list(pool.map(rejected, range(20)))
        assert store.db.execute("SELECT * FROM agent_token_entries ORDER BY id").fetchall() == entries
        assert store.run_status_for("alice", response.run_id).status == "queued"
        assert len(store.messages_for("alice", session.id)) == 1
    finally:
        commit.set()
        gate.require_open = checked
        independent.close()


@pytest.mark.parametrize("claimed", [False, True])
def test_compute_new_work_blocked_but_ack_replay_and_completion_allowed(ledger_system, claimed):
    database, ledger, jobs = ledger_system
    gate = make_gate(database)
    jobs.admission_gate = gate
    leases = ComputeLeases(database, ledger, admission_gate=gate)
    claim = ComputeClaimRequest(service_id="gpu", worker_id="receiver", resources=WorkerResources(gpu_uuids=["gpu0"]))
    job = jobs.submit("alice", request(), "old")
    grant = leases.claim(claim) if claimed else None
    before = ledger.usage_for("alice")
    gate.enter(identity())
    assert jobs.submit("alice", request(), "old").id == job.id
    with pytest.raises(AdmissionClosed):
        jobs.submit("alice", request(), "new")
    if grant:
        replay = leases.claim(claim)
        assert replay.recovered and replay.fencing_token == grant.fencing_token
        assert replay.job.id == job.id
    else:
        assert leases.claim(claim) is None
    assert ledger.usage_for("alice") == before
    if not claimed:
        assert jobs.cancel("alice", job.id).status == "cancelled"
        assert ledger.usage_for("alice").gpu.reserved == 0


def test_legacy_af3_submission_and_receiver_claims_obey_same_gate(system):
    _database, gate, store = system
    job = store.create_af3_job("alice", 1, simulation=True, idempotency_key="old")
    before = store.usage_for("alice")
    gate.enter(identity())
    assert store.create_af3_job("alice", 1, simulation=True, idempotency_key="old").id == job.id
    with pytest.raises(AdmissionClosed):
        store.create_af3_job("alice", 1, simulation=True, idempotency_key="new")
    resources = ComputeWorkerResources(gpu_count=1, gpu_memory_mb=80000, capabilities=["af3"])
    assert store.claim_compute_jobs("receiver", resources) == []
    assert store.usage_for("alice") == before


def test_workspace_create_and_new_leases_block_but_confirm_allowed(system):
    database, gate, _store = system
    owners = WorkspaceSandboxStore(database, admission_gate=gate)
    image = "synthetic@sha256:" + "a" * 64
    claim = owners.claim_creation("alice", provider="opensandbox", image_digest=image)
    spare = owners.claim_creation("bob", provider="opensandbox", image_digest=image)
    stopped = owners.confirm_instance("bob", claim_token=spare.record.claim_token, sandbox_id="stopped",
                                      runtime_state="stopped")
    gate.enter(identity())
    ready = owners.confirm_instance("alice", claim_token=claim.record.claim_token, sandbox_id="existing")
    assert ready.lifecycle_state == "ready"
    assert not owners.claim_creation("alice", provider="opensandbox", image_digest=image).claimed
    with pytest.raises(AdmissionClosed):
        owners.claim_creation("charlie", provider="opensandbox", image_digest=image)
    with pytest.raises(AdmissionClosed):
        owners.acquire_lease(attempt_id="new", user_id="alice", session_id="one", run_id="run", lease_seconds=60)
    with pytest.raises(AdmissionClosed):
        owners.begin_drain("alice", ready.provider_revision)
    with pytest.raises(AdmissionClosed):
        owners.begin_replace("bob", stopped.provider_revision)


def test_workspace_attempt_claim_is_durable_before_provider_dispatch(system):
    database, gate, _store = system
    attempts = WorkspaceAttemptStore(database, admission_gate=gate)
    existing = attempts.create(attempt_id="existing", run_id="run", session_id="one", user_id="alice",
                               fencing_token=1, reserved_cpu_core_ms=0)
    gate.enter(identity())
    with pytest.raises(AdmissionClosed):
        attempts.create(attempt_id="new", run_id="run", session_id="one", user_id="alice",
                        fencing_token=1, reserved_cpu_core_ms=0)
    with pytest.raises(AdmissionClosed):
        attempts.reserve_cpu(existing.attempt_id, 1, 100, 1000, claim=True)
    assert attempts.get(existing.attempt_id) == existing
    gate.release(identity())
    claimed = attempts.reserve_cpu(existing.attempt_id, 1, 100, 1000, claim=True)
    assert claimed.status == "running" and claimed.provider_process_id is None
    gate.enter(identity())
    assert attempts.mark_running(existing.attempt_id, 1, "ack-process").provider_process_id == "ack-process"
    finished = attempts.finish(existing.attempt_id, 1, status="completed", wall_ms=10, cpu_core_ms=10,
                               peak_memory_bytes=None, exit_code=0, output_truncated=False)
    assert finished.status == "completed" and finished.cpu_core_ms == 10


@pytest.mark.asyncio
async def test_command_service_commits_active_record_before_external_process(system):
    database, gate, _store = system
    attempts = WorkspaceAttemptStore(database, admission_gate=gate)
    attempts.create(attempt_id="existing", run_id="run", session_id="one", user_id="alice",
                    fencing_token=1, reserved_cpu_core_ms=0)
    calls = []

    async def capabilities():
        return WorkspaceCapabilities(provider="opensandbox", runtime="runsc", command_execution=True,
                                     command_events=True, session_mount_namespace=True)

    async def ensure(user):
        assert attempts.get("existing").status == "running"
        calls.append("ensure")
        gate.enter(identity())
        return WorkspaceSandbox(user, "owned", "volume", "digest", 1, "ready")

    async def start(_sandbox, _request):
        assert attempts.get("existing").status == "running"
        assert gate.verify(identity())["state"] == "closed"
        calls.append("start")
        return WorkspaceCommandHandle("owned", "existing", "process")

    provider = SimpleNamespace(capabilities=capabilities, ensure_user=ensure, commands=SimpleNamespace(start=start))
    service = WorkspaceCommands(provider, attempts, cpu_daily_limit_ms=1000, cpu_millicores=1000)
    result = await service.start(WorkspaceOperationContext("alice", "one", "existing"), run_id="run",
                                 argv=("python", "-V"), cwd=None, timeout_seconds=0.1, max_output_bytes=100)
    assert result == "existing" and calls == ["ensure", "start"]
    assert attempts.get("existing").provider_process_id == "process"


def test_explicit_migration_and_startup_never_reset_hold_or_rebind(system):
    database, gate, _store = system
    gate.enter(identity())
    migrate_postgres(database.pool.conninfo, schema=database.schema)
    restarted = make_gate(database)
    assert restarted.verify(identity())["state"] == "closed"
    with database.connection() as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == list(range(1, 14))
        assert SCHEMA_VERSION == 13
        before = connection.execute("SELECT * FROM release_admission_state").fetchall()
    different = DurableAdmissionGate(database, environment="prod", project="pskit-agent-cloud", qualification_sha256="a" * 64)
    with pytest.raises(AdmissionUnavailable):
        different.configure()
    with database.connection() as connection:
        assert connection.execute("SELECT * FROM release_admission_state").fetchall() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("executor", ["mock", "disabled"])
@pytest.mark.parametrize("injected", [True, False])
async def test_gated_factory_wraps_actual_injected_mcp_and_checks_consumers(pg_schema, executor, injected):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    migrate_admission_postgres(dsn, schema=schema)
    class Provider(MockMcp):
        invoked = 0

        async def invoke(self, name, arguments):
            self.invoked += 1
            return await super().invoke(name, arguments)

    provider = Provider()
    app = create_app(Settings(
        mode="live", agent_runtime="pi", database_url=dsn, database_schema=schema,
        auth_csrf_secret="s" * 40, supabase_url="https://identity.example",
        supabase_publishable_key="synthetic-public", model_gateway_base_url="https://models.example/v1",
        model_gateway_model="synthetic", mcp_executor=executor, af3_executor="disabled",
        release_admission_enabled=True, release_admission_environment="staging",
        release_admission_project="pskit-agent-staging", release_admission_key="k" * 40,
        release_admission_qualification_sha256="a" * 64,
    ), pi_runner=SimpleNamespace(), mcp_provider=provider if injected else None)
    app.dependency_overrides[get_current_user] = lambda: UserIdentity(id="alice", email="alice@example.org", name="Alice")
    app.state.identity_policy.observe_verified_user("alice", False)
    try:
        gate = app.state.admission_gate
        gate.enter(identity())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://private.test") as client:
            proof_headers = {"Authorization": "Bearer " + "k" * 40}
            proof = await client.get("/internal/release-admission?challenge=" + "e" * 64, headers=proof_headers)
            assert proof.status_code == 200
            denied = await client.post("/api/v1/mcp/tools/search_pdb/invoke", json={"query": "RNA"})
            assert denied.status_code == 503
            assert provider.invoked == 0
            app.state.pdf_processor._shared = None
            detached = await client.get("/internal/release-admission?challenge=" + "e" * 64, headers=proof_headers)
            assert detached.status_code == 503
            app.state.pdf_processor._shared = app.state.pdf_capacity
            qualification = app.state.mcp_qualification.executor
            assert qualification.calls is app.state.mcp_tool_calls
            assert qualification.capacity.shared is app.state.mcp_capacity
            qualification.capacity.shared = None
            detached = await client.get("/internal/release-admission?challenge=" + "e" * 64, headers=proof_headers)
            assert detached.status_code == 503
            qualification.capacity.shared = app.state.mcp_capacity
            if hasattr(app.state.mcp, "shared"):
                app.state.mcp.shared = None
                detached = await client.get("/internal/release-admission?challenge=" + "e" * 64, headers=proof_headers)
                assert detached.status_code == 503
    finally:
        app.state.database.close()


def test_repeatable_read_waiter_never_uses_pre_close_open_snapshot(system, pg_schema):
    database, gate, store = system
    dsn, schema = pg_schema
    session = store.create_session("alice", store.project_for("alice").id, "Synthetic")
    application = "repeatable-admission-" + uuid.uuid4().hex
    alternate = PostgresDatabase(make_conninfo(
        dsn, application_name=application, options="-c default_transaction_isolation=repeatable\\ read",
    ), schema=schema)
    other_gate = make_gate(alternate)
    other_store = PersistentConversationStore(alternate, admission_gate=other_gate)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.transaction() as connection:
                # Publish closure while holding the same lock the real enter
                # operation uses; the waiter establishes its older MVCC snapshot.
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission',0))")
                connection.execute(
                    "UPDATE release_admission_state SET state='closed',identity_json=%s,generation=generation+1",
                    (json.dumps(identity()),),
                )
                attempted = pool.submit(other_store.accept_message, "alice", session.id,
                                        MessageRequest(content="must not use stale OPEN"), None, 100, "new", "new")
                deadline = time.monotonic() + 2
                waiting = False
                with psycopg.connect(dsn, autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        waiting = observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                            "AND wait_event='advisory')", (application,),
                        ).fetchone()[0]
                        if waiting:
                            break
                        time.sleep(0.01)
                assert waiting
            with pytest.raises(AdmissionUnavailable):
                attempted.result(timeout=3)
        assert store.messages_for("alice", session.id) == []
        assert store.db.execute("SELECT count(*) FROM agent_token_entries").fetchone()[0] == 0
        assert gate.verify(identity())["state"] == "closed"
    finally:
        alternate.close()


@pytest.mark.asyncio
async def test_observation_refuses_missing_shared_qualification_capacity(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    migrate_admission_postgres(dsn, schema=schema)
    app = create_app(Settings(
        mode="live", agent_runtime="pi", database_url=dsn, database_schema=schema,
        auth_csrf_secret="s" * 40, supabase_url="https://identity.example",
        supabase_publishable_key="synthetic-public", model_gateway_base_url="https://models.example/v1",
        model_gateway_model="synthetic", mcp_executor="disabled", af3_executor="disabled",
        release_admission_enabled=True, release_admission_environment="staging",
        release_admission_project="pskit-agent-staging", release_admission_key="k" * 40,
        release_admission_qualification_sha256="a" * 64,
    ), pi_runner=SimpleNamespace())
    try:
        app.state.admission_gate.enter(identity())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://private.test") as client:
            headers = {"Authorization": "Bearer " + "k" * 40}
            url = "/internal/release-admission?challenge=" + "e" * 64
            assert (await client.get(url, headers=headers)).status_code == 200
            app.state.mcp_capacity = None
            app.state.mcp_qualification.executor.capacity.shared = None
            refused = await client.get(url, headers=headers)
            assert refused.status_code == 503
            assert refused.json() == {"detail": {"code": "ADMISSION_ENFORCEMENT_UNVERIFIED"}}
    finally:
        app.state.database.close()


def test_gate_enabled_startup_refuses_missing_component_without_installing_it(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    with pytest.raises(AdmissionUnavailable):
        create_app(Settings(
            mode="live", agent_runtime="pi", database_url=dsn, database_schema=schema,
            auth_csrf_secret="s" * 40, supabase_url="https://identity.example",
            supabase_publishable_key="synthetic-public", model_gateway_base_url="https://models.example/v1",
            model_gateway_model="synthetic", mcp_executor="disabled", af3_executor="disabled",
            release_admission_enabled=True, release_admission_environment="staging",
            release_admission_project="pskit-agent-staging", release_admission_key="k" * 40,
            release_admission_qualification_sha256="a" * 64,
        ), pi_runner=SimpleNamespace())
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
            "AND table_name LIKE 'release_admission%%'", (schema,),
        ).fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM " + schema + ".schema_migrations",
        ).fetchone() == (SCHEMA_VERSION,)
