"""Administrator limits govern real admission and uploads across database connections."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.contracts.admin import LimitsUpdate
from app.contracts.catalog import FileUploadRequest
from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.admin.audit import AdminOperations
from app.domain.admin.roles import AdminStore
from app.domain.catalog import CatalogStore, InvalidFileUpload
from app.domain.identity_policy import IdentityPolicyStore
from app.domain.persistent_conversation import PersistentConversationStore


def prepare(pg_schema, storage=10, concurrency=1):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    db = PostgresDatabase(dsn, schema=schema)
    identities = IdentityPolicyStore(db)
    identities.observe_verified_user("member", False)
    quotas = PersistentConversationStore(db)
    quotas.identity_policy = identities
    admin = AdminStore(db, identity_policy=identities, quotas=quotas)
    admin.grant("member", "platform_admin", actor="server:test", reason="Test grant")
    AdminOperations(admin).update_limits(
        admin.principal("member"),
        "member",
        LimitsUpdate(
            expected_revision=0,
            reason="Test resource allowance",
            token_monthly_limit=10000,
            gpu_daily_minutes=60,
            concurrency_limit=concurrency,
            storage_limit_bytes=storage,
        ),
    )
    db.close()


def test_two_file_uploads_cannot_exceed_admin_storage(pg_schema):
    prepare(pg_schema)
    dsn, schema = pg_schema
    barrier = Barrier(2)

    def save(i):
        db = PostgresDatabase(dsn, schema=schema)
        try:
            admin = AdminStore(db)
            catalog = CatalogStore(db_path=db)
            catalog.admin_storage_limit_for = admin.storage_limit_for
            barrier.wait(timeout=5)
            try:
                catalog.add_file(
                    "member", FileUploadRequest(name=f"n{i}.md", size=6, content="123456")
                )
                return "saved"
            except InvalidFileUpload as exc:
                assert exc.code == "STORAGE_QUOTA_EXCEEDED"
                return "rejected"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [1, 2])) == ["rejected", "saved"]


def test_admin_concurrency_limits_actual_pi_claims(pg_schema):
    prepare(pg_schema)
    dsn, schema = pg_schema
    db = PostgresDatabase(dsn, schema=schema)
    store = PersistentConversationStore(db)
    project = store.project_for("member")
    sessions = [store.create_session("member", project.id, str(i)) for i in range(2)]
    runs = [
        store.send_message("member", s.id, MessageRequest(content="Run analysis")).run_id
        for s in sessions
    ]
    db.close()
    barrier = Barrier(2)

    def claim(run_id):
        database = PostgresDatabase(dsn, schema=schema)
        try:
            admin = AdminStore(database)
            current = PersistentConversationStore(database)
            current.admin_concurrency_limit_for = admin.concurrency_limit_for
            barrier.wait(timeout=5)
            return current.claim_initial_run(
                run_id, "worker-" + run_id, max_active=4, max_user_active=2
            )
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, runs)) == [False, True]


def test_admin_concurrency_limits_compute_worker_claims(pg_schema):
    from app.contracts.compute import (
        CapabilityVersion,
        ComputeBudget,
        ComputeClaimRequest,
        ComputeJobRequest,
        ComputeServiceManifest,
        WorkerResources,
    )
    from app.domain.compute.jobs import ComputeJobs
    from app.domain.compute.leases import ComputeLeases
    from app.domain.compute.ledger import ComputeLedger

    prepare(pg_schema, concurrency=1)
    dsn, schema = pg_schema
    db = PostgresDatabase(dsn, schema=schema)
    try:
        ledger = ComputeLedger(db, gpu_daily_limit_ms=3600000)
        jobs = ComputeJobs(db, ledger)
        jobs.catalog.register(
            ComputeServiceManifest(
                service_id="gpu",
                model_version="v1",
                capabilities=[
                    CapabilityVersion(
                        id="gpu.predict",
                        version="1",
                        visibility="published",
                        input_schema={"type": "object"},
                        gpu_count=1,
                        required_usage=["gpu_device_ms"],
                        concurrency=10,
                        max_budget=ComputeBudget(gpu_device_ms=60000),
                    )
                ],
            )
        )
        for i in range(2):
            jobs.submit(
                "member",
                ComputeJobRequest(
                    capability_id="gpu.predict",
                    version="1",
                    arguments={},
                    budget=ComputeBudget(gpu_device_ms=10000),
                ),
                f"job-{i}",
            )
        leases = ComputeLeases(db, ledger)
        assert (
            leases.claim(
                ComputeClaimRequest(
                    service_id="gpu",
                    worker_id="first",
                    resources=WorkerResources(gpu_uuids=["gpu-1"]),
                )
            )
            is not None
        )
        assert (
            leases.claim(
                ComputeClaimRequest(
                    service_id="gpu",
                    worker_id="second",
                    resources=WorkerResources(gpu_uuids=["gpu-2"]),
                )
            )
            is None
        )
    finally:
        db.close()


def test_artifact_bytes_reduce_remaining_file_storage(pg_schema):
    from app.contracts.conversation import MessageRequest
    from app.domain.sandboxes import SandboxArtifactStore

    prepare(pg_schema, storage=10)
    dsn, schema = pg_schema
    db = PostgresDatabase(dsn, schema=schema)
    try:
        conversations = PersistentConversationStore(db)
        project = conversations.project_for("member")
        session = conversations.create_session("member", project.id, "Artifact storage")
        run = conversations.send_message(
            "member", session.id, MessageRequest(content="Produce artifact")
        )
        artifact_store = SandboxArtifactStore(conversations.db)
        artifact_store.put("member", session.id, run.run_id, "output.txt", b"123456")
        catalog = CatalogStore(db_path=db)
        catalog.admin_storage_limit_for = AdminStore(db).storage_limit_for
        assert catalog.stored_bytes_for("member") == 6
        with pytest.raises(InvalidFileUpload):
            catalog.add_file("member", FileUploadRequest(name="new.md", size=5, content="12345"))
    finally:
        db.close()
