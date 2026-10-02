import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.contracts.capabilities import Af3FoldInput, ComputeWorkerResources
from app.domain.persistent_conversation import ComputeLeaseConflict, PersistentConversationStore


FOLD_INPUT = {
    "name": "small protein", "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3", "version": 4,
}
WORKER = {"worker_id": "a6000", "resources": {
    "capabilities": ["af3"], "gpu_count": 1, "gpu_memory_mb": 49_152,
}}


@pytest.mark.asyncio
async def test_timed_out_running_af3_holds_gpu_budget_until_late_usage_report(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
        af3_execution_timeout_seconds=60,
    ), pi_runner=object())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            login = (await client.post("/api/v1/auth/demo",
                                       json={"email": "alice@example.org"})).json()
            user = {"Authorization": f"Bearer {login['access_token']}"}
            created = (await client.post("/api/v1/af3/jobs", headers=user,
                                         json={"estimated_gpu_minutes": 20,
                                               "fold_input": FOLD_INPUT})).json()
            job_id = created["id"]
            key = {"X-Compute-Key": "compute-key"}
            claimed = (await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                         json=WORKER)).json()[0]
            now += timedelta(seconds=61)
            for _ in range(100):
                job = (await client.get(f"/api/v1/af3/jobs/{job_id}", headers=user)).json()
                if job["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            before = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]
            late = await client.post(f"/internal/af3/jobs/{job_id}/result", headers=key,
                                     json={"status": "failed", "actual_gpu_minutes": 12,
                                           "attempt": claimed["attempt"],
                                           "lease_token": claimed["lease_token"]})
            after = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]

    assert job["status"] == "failed"
    assert job["gpu_accounting_status"] == "pending_reconciliation"
    assert (before["used"], before["reserved"], before["remaining"]) == (0, 20, 40)
    assert late.status_code == 200
    assert late.json()["status"] == "failed"
    assert late.json()["gpu_accounting_status"] == "reconciled"
    assert late.json()["actual_gpu_minutes"] == 12
    assert (after["used"], after["reserved"], after["remaining"]) == (12, 0, 48)


def test_claimed_cancel_keeps_budget_until_authenticated_usage_reconciliation(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    fold_input = Af3FoldInput.model_validate(FOLD_INPUT)
    created = store.create_af3_job("alice", 20, fold_input=fold_input)
    resources = ComputeWorkerResources.model_validate(WORKER["resources"])
    claimed = store.claim_compute_jobs("a6000", resources)[0]

    cancelled = store.cancel_af3_job("alice", created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.gpu_accounting_status == "pending_reconciliation"
    assert store.usage_for("alice").gpu.reserved == 20
    assert store.usage_entries_for("alice")[-1].status == "pending_reconciliation"

    with pytest.raises(ComputeLeaseConflict):
        store.settle_af3_job(created.id, "failed", 7, [],
                             attempt=claimed.attempt, lease_token="wrong")
    with pytest.raises(ComputeLeaseConflict):
        store.settle_af3_job(created.id, "failed", 7, [],
                             attempt=claimed.attempt + 1, lease_token=claimed.lease_token)
    assert store.usage_for("alice").gpu.reserved == 20

    reconciled = store.settle_af3_job(created.id, "failed", 7, [],
                                      attempt=claimed.attempt,
                                      lease_token=claimed.lease_token)
    assert reconciled.status == "cancelled"
    assert reconciled.gpu_accounting_status == "reconciled"
    assert reconciled.actual_gpu_minutes == 7
    assert store.usage_for("alice").gpu.used == 7
    assert store.usage_for("alice").gpu.reserved == 0
    assert store.usage_entries_for("alice")[-1].status == "reconciled"
    audit = store.gpu_reconciliations_for(created.id)
    assert [(item.source, item.previous_minutes, item.actual_minutes)
            for item in audit] == [("worker", None, 7)]
    assert store.settle_af3_job(created.id, "failed", 7, [],
                                attempt=claimed.attempt,
                                lease_token=claimed.lease_token).actual_gpu_minutes == 7
    with pytest.raises(ValueError, match="usage"):
        store.settle_af3_job(created.id, "failed", 8, [],
                             attempt=claimed.attempt, lease_token=claimed.lease_token)


def test_unclaimed_cancel_releases_gpu_budget(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_af3_job("alice", 20,
                                   fold_input=Af3FoldInput.model_validate(FOLD_INPUT))
    cancelled = store.cancel_af3_job("alice", created.id)
    assert cancelled.gpu_accounting_status == "released"
    assert store.usage_for("alice").gpu.reserved == 0
    assert store.usage_entries_for("alice")[-1].amount == 0
