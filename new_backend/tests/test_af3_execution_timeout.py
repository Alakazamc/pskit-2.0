import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.contracts.capabilities import Af3FoldInput, ComputeWorkerResources
from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import ComputeLeaseConflict, PersistentConversationStore
from app.main import create_app

WORKER_RESOURCES = ComputeWorkerResources(capabilities={"af3"}, gpu_count=1,
                                          gpu_memory_mb=49_152)
WORKER_HTTP_RESOURCES = {"resources": {"capabilities": ["af3"], "gpu_count": 1,
                                       "gpu_memory_mb": 49_152}}


def test_claimed_af3_job_has_a_final_execution_deadline(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    fold_input = Af3FoldInput.model_validate({
        "name": "small protein", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    })
    job = store.create_af3_job("alice", 20, fold_input=fold_input)
    claimed = store.claim_compute_jobs("a6000", WORKER_RESOURCES, lease_seconds=60)[0]
    assert claimed.id == job.id

    now += timedelta(seconds=45)
    assert store.renew_compute_lease(job.id, "a6000", claimed.lease_token,
                                     lease_seconds=60)
    now += timedelta(seconds=16)
    assert store.expire_running_compute_jobs(60) == 1
    assert store.get_af3_job("alice", job.id).status == "failed"
    assert store.usage_for("alice").gpu.reserved == 20
    assert store.renew_compute_lease(job.id, "a6000", claimed.lease_token) is False
    assert store.settle_af3_job(job.id, "completed", 10, [],
                                attempt=claimed.attempt,
                                lease_token=claimed.lease_token).status == "failed"
    assert store.usage_for("alice").gpu.used == 10
    assert store.usage_for("alice").gpu.reserved == 0
    assert store.expire_running_compute_jobs(60) == 0
    assert store.claim_compute_jobs("other", WORKER_RESOURCES) == []


def test_worker_cannot_reclaim_job_past_execution_deadline_before_scheduler_sweep(
    tmp_path, monkeypatch,
):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    fold_input = Af3FoldInput.model_validate({
        "name": "small protein", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    })
    store.create_af3_job("alice", 20, fold_input=fold_input)
    assert len(store.claim_compute_jobs("a6000", WORKER_RESOURCES, lease_seconds=5)) == 1
    now += timedelta(seconds=61)
    assert store.claim_compute_jobs("other", WORKER_RESOURCES,
                                    max_execution_seconds=60) == []
    assert store.expire_running_compute_jobs(60) == 1


def test_worker_cannot_extend_or_complete_past_deadline_before_scheduler_sweep(
    tmp_path, monkeypatch,
):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    fold_input = Af3FoldInput.model_validate({
        "name": "small protein", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    })
    job = store.create_af3_job("alice", 20, fold_input=fold_input)
    claimed = store.claim_compute_jobs("a6000", WORKER_RESOURCES, lease_seconds=120)[0]
    now += timedelta(seconds=61)
    assert not store.renew_compute_lease(
        job.id, "a6000", claimed.lease_token, max_execution_seconds=60,
    )
    with pytest.raises(ComputeLeaseConflict):
        store.settle_af3_job(job.id, "completed", 10, [], attempt=claimed.attempt,
                             lease_token=claimed.lease_token, max_execution_seconds=60)
    with pytest.raises(ComputeLeaseConflict):
        store.save_artifact_blob(
            job.id, "artifact-1", "result.cif", "structure", b"result",
            attempt=claimed.attempt, lease_token=claimed.lease_token,
            max_execution_seconds=60,
        )
    assert store.get_af3_job("alice", job.id).status == "running"


def test_execution_timeout_fails_waiting_run_once(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    run = store.send_message("alice", "session-alice", MessageRequest(content="Predict structure"))
    assert run is not None
    store.set_run_status(run.run_id, "waiting")
    fold_input = Af3FoldInput.model_validate({
        "name": "small protein", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    })
    job = store.create_af3_job("alice", 20, run_id=run.run_id,
                               tool_call_id="call-1", fold_input=fold_input)
    store.claim_compute_jobs("a6000", WORKER_RESOURCES)
    now += timedelta(seconds=61)
    assert store.expire_running_compute_jobs(60) == 1
    assert store.expire_running_compute_jobs(60) == 0
    assert store.run_status_for("alice", run.run_id).status == "failed"
    events = store.events_for("alice", run.run_id, None)
    assert [event.data.code for event in events if event.type == "run.failed"] == [
        "AF3_EXECUTION_TIMEOUT",
    ]
    assert store.get_af3_job("alice", job.id).status == "failed"


@pytest.mark.asyncio
async def test_callback_scheduler_expires_running_job_without_worker_result(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
        af3_execution_timeout_seconds=60,
    ), pi_runner=object())
    fold_input = Af3FoldInput.model_validate({
        "name": "small protein", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    })
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        app.state.identity_policy.observe_verified_user("alice", False)
        job = app.state.conversations.create_af3_job("alice", 20, fold_input=fold_input)
        claimed = await client.post(
            "/internal/compute/af3/jobs/claim", headers={"X-Compute-Key": "compute-key"},
            json={"worker_id": "a6000", **WORKER_HTTP_RESOURCES},
        )
        assert claimed.status_code == 200 and len(claimed.json()) == 1
        now += timedelta(seconds=61)
        for _ in range(100):
            if app.state.conversations.get_af3_job("alice", job.id).status == "failed":
                break
            await asyncio.sleep(0.01)
        assert app.state.conversations.get_af3_job("alice", job.id).status == "failed"


def test_execution_timeout_must_be_positive():
    with pytest.raises(ValueError, match="AF3_EXECUTION_TIMEOUT_SECONDS"):
        create_app(Settings(af3_execution_timeout_seconds=0))
