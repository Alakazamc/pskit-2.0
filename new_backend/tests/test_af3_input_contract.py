import httpx
import pytest

from app.config import Settings
from app.contracts.capabilities import Af3FoldInput
from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app

FOLD_INPUT = {
    "name": "RNA complex",
    "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3",
    "version": 4,
}


@pytest.mark.asyncio
async def test_af3_input_is_validated_persisted_and_owned_by_submitter(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                        af3_executor="callback", compute_callback_key="test-compute-key")
    first = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        bad = await client.post("/api/v1/af3/jobs", headers=a,
                                json={"fold_input": {**FOLD_INPUT, "modelSeeds": []}})
        unsafe = await client.post("/api/v1/af3/jobs", headers=a,
                                   json={"fold_input": {**FOLD_INPUT, "userCCDPath": "/private/file"}})
        before = (await client.get("/api/v1/usage", headers=a)).json()["gpu"]
        created = await client.post("/api/v1/af3/jobs", headers=a,
                                    json={"estimated_gpu_minutes": 20, "fold_input": FOLD_INPUT})
        job_id = created.json()["id"]

    second = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        restored = await client.get(f"/api/v1/af3/jobs/{job_id}", headers={
            "Authorization": f"Bearer {alice['access_token']}",
        })
        compute = await client.get(f"/internal/compute/af3/jobs/{job_id}", headers={
            "X-Compute-Key": "test-compute-key",
        })
        unauthenticated_compute = await client.get(f"/internal/compute/af3/jobs/{job_id}")
        foreign = await client.get(f"/api/v1/af3/jobs/{job_id}", headers={
            "Authorization": f"Bearer {bob['access_token']}",
        })

    assert bad.status_code == unsafe.status_code == 422
    assert before["reserved"] == 0
    assert created.status_code == restored.status_code == 200
    assert restored.json()["fold_input"] == FOLD_INPUT
    assert compute.status_code == 200 and compute.json()["fold_input"] == FOLD_INPUT
    assert unauthenticated_compute.status_code == 404
    assert foreign.status_code == 404


def test_approved_af3_job_keeps_original_input_and_rejects_changed_retry(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    accepted = store.accept_message("alice", "session-alice", MessageRequest(content="AF3"),
                                    store, 2, None, "request-hash")
    assert accepted is not None
    run_id = accepted[0].run_id
    fold_input = Af3FoldInput.model_validate(FOLD_INPUT)
    approval = store.request_af3_approval("alice", run_id, "call-1", 40, fold_input)
    store.set_run_status(run_id, "waiting")
    decision = store.decide_af3_approval("alice", run_id, approval.approval_id, "approved")

    assert decision is not None and decision.job_id is not None
    assert store.get_af3_job("alice", decision.job_id).fold_input == FOLD_INPUT
    with pytest.raises(ValueError, match="arguments conflict"):
        store.request_af3_approval("alice", run_id, "call-1", 40,
                                   Af3FoldInput.model_validate({**FOLD_INPUT, "name": "Changed"}))


@pytest.mark.asyncio
async def test_internal_af3_retry_with_changed_input_returns_conflict(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              af3_executor="callback", compute_callback_key="test-compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={
            "email": "alice@example.org",
        })).json()
        user_id = identity["user"]["id"]
        accepted = app.state.conversations.accept_message(
            user_id, f"session-{user_id}", MessageRequest(content="AF3"), app.state.quotas,
            2, None, "request-hash",
        )
        assert accepted is not None
        run_id = accepted[0].run_id
        app.state.conversations.claim_initial_run(run_id)
        authorization = {"Authorization": f"Bearer {app.state.agent_service.tool_token(run_id)}"}
        first = await client.post("/internal/af3/jobs", headers=authorization, json={
            "run_id": run_id, "tool_call_id": "call-1", "estimated_gpu_minutes": 20,
            "fold_input": FOLD_INPUT,
        })
        changed = await client.post("/internal/af3/jobs", headers=authorization, json={
            "run_id": run_id, "tool_call_id": "call-1", "estimated_gpu_minutes": 20,
            "fold_input": {**FOLD_INPUT, "name": "Changed"},
        })

    assert first.status_code == 200
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "AF3_ARGUMENT_CONFLICT"
