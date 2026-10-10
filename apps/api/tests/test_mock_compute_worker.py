import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app
from scripts.mock_af3_worker import MockAf3Worker


def test_mock_timer_result_is_labeled_simulated_and_default_callback_is_not(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    simulated = store.create_af3_job("alice", 5)
    store.advance_mock_jobs(1)
    now += timedelta(seconds=2)
    store.advance_mock_jobs(1)
    store.advance_mock_jobs(1)
    real = store.create_af3_job("bob", 5)
    result = store.settle_af3_job(real.id, "completed", 2, [])
    assert store.get_af3_job("alice", simulated.id).simulation is True
    assert result.simulation is False


@pytest.mark.asyncio
async def test_mock_compute_worker_claims_and_completes_input_job(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
    ), pi_runner=object())
    fold_input = {
        "name": "mock RNA complex", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3", "version": 4,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 5, "fold_input": fold_input})
        worker = MockAf3Worker(client, compute_key="compute-key", worker_id="mock-a6000")
        count = await worker.run_once()
        idle = await worker.run_once()
        job = (await client.get(f"/api/v1/af3/jobs/{created.json()['id']}", headers=user)).json()
        artifacts = (await client.get("/api/v1/artifacts", headers=user)).json()
        downloaded = await client.get(
            f"/api/v1/artifacts/{artifacts[0]['id']}/download", headers=user,
        )
    assert count == 1 and idle == 0
    assert job["status"] == "completed" and job["actual_gpu_minutes"] == 1
    assert job["simulation"] is True
    assert artifacts[0]["available"] is True
    result = json.loads(downloaded.content)
    assert result["simulation"] is True
    assert result["input_name"] == "mock RNA complex"
