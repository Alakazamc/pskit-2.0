"""Durable public computation admission, ownership and version boundaries."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import get_current_user
from app.api.compute import router
from app.contracts.compute import CapabilityVersion, ComputeJobRequest, ComputeServiceManifest
from app.contracts.models import UserIdentity
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.jobs import ComputeJobs


@pytest.fixture
def computation(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        catalog = ComputeCatalog(database)
        catalog.register(ComputeServiceManifest(service_id="lab", model_version="v1", capabilities=[
            CapabilityVersion(id="lab.inspect", version="1", visibility="published",
                              input_schema={"type": "object", "properties": {"sequence": {
                                  "type": "string"}}, "required": ["sequence"],
                                            "additionalProperties": False}),
        ]))
        yield database, catalog, ComputeJobs(database)
    finally:
        database.close()


def test_api_rejects_private_versions_and_cross_user_jobs(computation):
    _database, catalog, jobs = computation
    app = FastAPI()
    app.state.compute_jobs = jobs
    app.state.compute_catalog = catalog
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserIdentity(
        id="alice", email="alice@example.org", name="Alice",
    )
    client = TestClient(app)
    payload = {"capability_id": "lab.inspect", "version": "1", "arguments": {"sequence": "ACG"}}
    created = client.post("/api/v1/compute/jobs", json=payload, headers={"Idempotency-Key": "one"})
    assert created.status_code == 200
    job_id = created.json()["id"]
    assert created.json()["user_id"] == "alice"
    assert created.json()["run_id"] is None
    assert client.post("/api/v1/compute/jobs", json={**payload, "user_id": "bob"}, headers={"Idempotency-Key": "owner"}).status_code == 422
    assert client.post("/api/v1/compute/jobs", json={**payload, "version": "draft"}, headers={"Idempotency-Key": "draft"}).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: UserIdentity(
        id="bob", email="bob@example.org", name="Bob",
    )
    assert client.get(f"/api/v1/compute/jobs/{job_id}").status_code == 404
    assert client.post(f"/api/v1/compute/jobs/{job_id}/cancel").status_code == 404


def test_concurrent_idempotency_is_durable_and_rejects_different_input(computation):
    _database, catalog, jobs = computation
    request = ComputeJobRequest(capability_id="lab.inspect", version="1", arguments={"sequence": "ACG"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: jobs.submit("alice", request, "shared-key").id, range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        jobs.submit("alice", request.model_copy(update={"arguments": {"sequence": "UGG"}}), "shared-key")
    with pytest.raises(ValueError, match="INVALID_ARGUMENTS"):
        jobs.submit("alice", request.model_copy(update={"arguments": {"sequence": 123}}), "invalid")
    with pytest.raises(ValueError, match="IMMUTABLE_VERSION"):
        catalog.register(ComputeServiceManifest(service_id="lab", model_version="changed", capabilities=[
            catalog.get("alice", "lab.inspect", "1")[1],
        ]))


def test_legacy_af3_cannot_claim_or_simulate_generic_job(computation):
    from app.contracts.capabilities import ComputeWorkerResources
    from app.domain.persistent_conversation import PersistentConversationStore

    database, _catalog, jobs = computation
    job = jobs.submit("alice", ComputeJobRequest(capability_id="lab.inspect", version="1",
                                              arguments={"sequence": "ACG"}), "not-af3")
    old = PersistentConversationStore(database)
    assert old.get_af3_job("alice", job.id) is None
    assert old.claim_compute_jobs("old-worker", ComputeWorkerResources(
        capabilities=["af3"], gpu_count=1, gpu_memory_mb=80000,
    )) == []
    old.advance_mock_jobs(0)
    old.expire_queued_compute_jobs(0)
    assert jobs.get("alice", job.id).status == "queued"
