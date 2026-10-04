"""Fenced computation workers and transaction-backed result acknowledgements."""

import pytest
from compute_support import request

from app.contracts.compute import (
    Completed,
    ComputeClaimRequest,
    ComputeHeartbeatRequest,
    ComputeResultRequest,
    ExecutionError,
    Failed,
    UsageReport,
    WorkerResources,
)
from app.domain.compute.leases import ComputeLeases


def claim(service, worker="worker-a", gpu="GPU-1"):
    return service.claim(ComputeClaimRequest(service_id="gpu", worker_id=worker,
                                           resources=WorkerResources(gpu_uuids=[gpu])))


def report(grant, seq=1, amount=23000):
    return ComputeResultRequest(worker_id=grant.worker_id, attempt=grant.attempt,
        fencing_token=grant.fencing_token, seq=seq, stopped=True,
        report=Completed(result={"prediction": "ACG"}, usage=UsageReport(
            gpu_device_ms=amount, source="service_reported")))


def test_receipt_replay_cannot_charge_twice_or_overwrite(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    leases = ComputeLeases(database, ledger)
    grant = claim(leases)
    payload = report(grant)
    receipt = leases.complete("gpu", job.id, payload)
    assert leases.complete("gpu", job.id, payload) == receipt
    assert ledger.usage_for("alice").gpu.used == 23000
    assert jobs.get("alice", job.id).status == "completed"
    with pytest.raises(ValueError, match="RESULT_CONFLICT"):
        leases.complete("gpu", job.id, report(grant, amount=24000))
    with pytest.raises(ValueError, match="LEASE_NOT_OWNED"):
        leases.complete("gpu", job.id, payload.model_copy(update={"attempt": 2}))
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM compute_outbox").fetchone()[0] == 1


def test_heartbeat_never_resets_deadline_and_device_lock_survives_disconnect(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    leases = ComputeLeases(database, ledger)
    grant = claim(leases)
    heartbeat = ComputeHeartbeatRequest(worker_id=grant.worker_id, attempt=grant.attempt,
                                        fencing_token=grant.fencing_token, seq=1, progress=10)
    update = leases.heartbeat("gpu", job.id, heartbeat)
    assert update.stop_at == grant.stop_at
    with database.transaction() as connection:
        connection.execute("UPDATE agent_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' "
                           "WHERE id=%s", (job.id,))
    jobs.submit("bob", request(), "two")
    assert claim(leases, "worker-b") is None
    assert claim(leases).job.id == job.id
    assert ledger.usage_for("alice").gpu.reserved == 40000


def test_cancellation_waits_for_confirmed_stop_and_charges_failed_usage(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    leases = ComputeLeases(database, ledger)
    grant = claim(leases)
    assert jobs.cancel("alice", job.id).status == "cancelling"
    assert ledger.usage_for("alice").gpu.reserved == 40000
    payload = report(grant)
    with pytest.raises(ValueError, match="STOP_NOT_CONFIRMED"):
        leases.complete("gpu", job.id, payload.model_copy(update={"stopped": False}))
    payload = payload.model_copy(update={"report": Failed(error=ExecutionError(
        code="CANCELLED", message="stopped"), usage=UsageReport(
            gpu_device_ms=7000, source="service_reported"))})
    receipt = leases.complete("gpu", job.id, payload)
    assert receipt.status == "cancelled"
    assert ledger.usage_for("alice").gpu.used == 7000
    assert ledger.usage_for("alice").gpu.reserved == 0
    jobs.submit("bob", request(), "two")
    assert claim(leases, "worker-b").job.user_id == "bob"


def test_service_identity_cannot_read_or_settle_another_service_job(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    leases = ComputeLeases(database, ledger)
    grant = claim(leases)
    with pytest.raises(LookupError):
        leases.complete("other-service", job.id, report(grant))


def test_worker_http_requires_service_specific_credential(ledger_system):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.internal_compute import router

    database, ledger, jobs = ledger_system
    jobs.submit("alice", request(), "one")
    app = FastAPI()
    app.state.settings = SimpleNamespace(compute_service_keys_json='{"gpu":"worker-test-secret"}')
    app.state.compute_leases = ComputeLeases(database, ledger)
    app.include_router(router)
    client = TestClient(app)
    payload = {"service_id": "gpu", "worker_id": "worker-a", "resources": {"gpu_uuids": ["GPU-1"]}}
    assert client.post("/internal/compute/jobs/claim", json=payload).status_code == 404
    assert client.post("/internal/compute/jobs/claim", json=payload,
                       headers={"X-Compute-Key": "wrong"}).status_code == 404
    response = client.post("/internal/compute/jobs/claim", json=payload,
                           headers={"X-Compute-Key": "worker-test-secret"})
    assert response.status_code == 200
    grant = response.json()
    result = client.post(f"/internal/compute/jobs/{grant['job']['id']}/result",
        json={"worker_id": "worker-a", "attempt": grant["attempt"],
              "fencing_token": grant["fencing_token"], "seq": 1, "stopped": True,
              "report": {"status": "completed", "result": {}, "usage": {
                  "gpu_device_ms": 20000, "source": "service_reported"}}},
        headers={"X-Compute-Service": "gpu", "X-Compute-Key": "worker-test-secret"})
    assert result.status_code == 200
    assert result.json()["committed"] is True
