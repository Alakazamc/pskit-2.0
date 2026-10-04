"""A background computation resumes only its owning Pi session, including failure."""

import asyncio
from types import SimpleNamespace

import pytest
from compute_support import request

from app.contracts.compute import (
    ComputeClaimRequest,
    ComputeResultRequest,
    ExecutionError,
    Failed,
    UsageReport,
    WorkerResources,
)
from app.contracts.conversation import MessageRequest
from app.domain.compute.leases import ComputeLeases
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent import AgentService


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_generic_job_resumes_same_session_once_with_reported_token_accounting(ledger_system, outcome):
    from app.contracts.compute import Completed

    database, ledger, jobs = ledger_system
    store = PersistentConversationStore(database)
    run = store.send_message("alice", "session-alice", MessageRequest(content="Analyze RNA"))
    store.set_run_status(run.run_id, "running")
    store.set_session_file("alice", "session-alice", "/tmp/original-session.jsonl")
    job = jobs.submit("alice", request(), "agent-call", run_id=run.run_id, tool_call_id="call-1")
    store.set_run_status(run.run_id, "waiting")
    leases = ComputeLeases(database, ledger)
    grant = leases.claim(ComputeClaimRequest(service_id="gpu", worker_id="gpu-worker",
                                           resources=WorkerResources(gpu_uuids=["GPU-1"])))
    if outcome == "cancelled":
        jobs.cancel("alice", job.id)
    usage = UsageReport(gpu_device_ms=7000, source="service_reported")
    report = Completed(result={"score": 0.9}, usage=usage) if outcome == "completed" else Failed(
        error=ExecutionError(code="MODEL_FAILED", message="failed"), usage=usage)
    payload = ComputeResultRequest(worker_id=grant.worker_id, attempt=grant.attempt,
                                   fencing_token=grant.fencing_token, seq=1, stopped=True, report=report)
    receipt = leases.complete("gpu", job.id, payload)
    assert leases.complete("gpu", job.id, payload) == receipt
    prompts = []

    class Pi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            prompts.append((session_id, message, kwargs["session_file"]))
            await on_event({"type": "message_end", "message": {"role": "assistant",
                           "usage": {"totalTokens": 12}}})
            return {"text": "Analysis finished", "session_file": "/tmp/continued-session.jsonl"}

    service = AgentService(store, Pi(), "http://internal", 1, 0.01, af3_executor="disabled")
    service.compute_jobs = jobs
    await service.start()
    try:
        for _ in range(100):
            if store.run_status_for("alice", run.run_id).status == "completed":
                break
            await asyncio.sleep(0.02)
        assert store.run_status_for("alice", run.run_id).status == "completed"
    finally:
        await service.stop()
    assert prompts == [("session-alice", f"/pskit_resume {job.id}", "/tmp/original-session.jsonl")]
    assert store.usage_for("alice").tokens.used == 12
    assert [message.role for message in store.messages_for("alice", "session-alice")] == ["user", "assistant"]
    assert store.messages_for("bob", "session-alice") is None
    parts = store.messages_for("alice", "session-alice")[-1].parts
    results = [part for part in parts if part.type == "tool_result"]
    assert len(results) == 1
    assert results[0].tool == "submit_compute"
    assert results[0].result["report"]["usage"]["gpu_device_ms"] == 7000
    assert store.claim_wakeups("another") == []


def test_internal_submission_binds_agent_owner_and_independent_jobs_do_not_create_chat(ledger_system):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.internal_compute import router

    database, _ledger, jobs = ledger_system
    store = PersistentConversationStore(database)
    run = store.send_message("alice", "session-alice", MessageRequest(content="Analyze"))
    store.set_run_status(run.run_id, "running")
    service = AgentService(store, None, "http://internal", 1, 0.01)
    app = FastAPI()
    app.state.agent_service, app.state.conversations = service, store
    app.state.compute_jobs = jobs
    app.state.settings = SimpleNamespace(compute_service_keys_json='{}')
    app.include_router(router)
    client = TestClient(app)
    payload = {**request().model_dump(), "run_id": run.run_id, "tool_call_id": "call-1"}
    assert client.post("/internal/compute/jobs", json=payload).status_code == 401
    result = client.post("/internal/compute/jobs", json=payload,
                        headers={"Authorization": f"Bearer {service.tool_token(run.run_id)}"})
    assert result.status_code == 200
    assert result.json()["user_id"] == "alice"
    jobs.submit("bob", request(), "tool-page")
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs WHERE user_id='bob'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agent_messages WHERE user_id='bob'").fetchone()[0] == 0
