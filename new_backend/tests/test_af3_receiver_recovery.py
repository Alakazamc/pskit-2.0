from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app


FOLD_INPUT = {
    "name": "recovery test", "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3", "version": 4,
}
RESOURCES = {"capabilities": ["af3"], "gpu_count": 1, "gpu_memory_mb": 49152}


@pytest.mark.asyncio
async def test_expired_heartbeat_does_not_reassign_live_gpu_job(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 2, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "db.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        user = (await client.post("/api/v1/auth/demo", json={"email": "a@example.org"})).json()
        created = (await client.post("/api/v1/af3/jobs",
                                     headers={"Authorization": f"Bearer {user['access_token']}"},
                                     json={"fold_input": FOLD_INPUT})).json()
        key = {"X-Compute-Key": "compute-key"}
        claim = (await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                   json={"worker_id": "a6000", "lease_seconds": 60,
                                         "resources": RESOURCES})).json()[0]
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=61))
        owned = await client.get("/internal/compute/af3/jobs/owned", headers=key,
                                 params={"worker_id": "a6000"})
        competing = await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                      json={"worker_id": "other", "resources": RESOURCES})
        renewed = await client.post(
            f"/internal/compute/af3/jobs/{created['id']}/heartbeat", headers=key,
            json={"worker_id": "a6000", "lease_token": claim["lease_token"]},
        )
        progress = await client.post(
            f"/internal/compute/af3/jobs/{created['id']}/progress", headers=key,
            json={"worker_id": "a6000", "lease_token": claim["lease_token"],
                  "attempt": claim["attempt"], "progress": 35},
        )
        duplicate = await client.post(
            f"/internal/compute/af3/jobs/{created['id']}/progress", headers=key,
            json={"worker_id": "a6000", "lease_token": claim["lease_token"],
                  "attempt": claim["attempt"], "progress": 35},
        )
        stale = await client.post(
            f"/internal/compute/af3/jobs/{created['id']}/progress", headers=key,
            json={"worker_id": "other", "lease_token": "wrong", "attempt": 1,
                  "progress": 99},
        )
    assert competing.json() == []
    assert owned.status_code == 200
    assert owned.json()[0]["lease_token"] == claim["lease_token"]
    assert renewed.status_code == progress.status_code == duplicate.status_code == 200
    assert progress.json()["progress"] == duplicate.json()["progress"] == 35
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_result_after_network_outage_is_accepted_with_original_token(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 2, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "db.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        user = (await client.post("/api/v1/auth/demo", json={"email": "a@example.org"})).json()
        job = (await client.post("/api/v1/af3/jobs",
                                 headers={"Authorization": f"Bearer {user['access_token']}"},
                                 json={"fold_input": FOLD_INPUT})).json()
        key = {"X-Compute-Key": "compute-key"}
        claim = (await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                   json={"worker_id": "a6000", "lease_seconds": 60,
                                         "resources": RESOURCES})).json()[0]
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=120))
        uploaded = await client.put(
            f"/internal/af3/jobs/{job['id']}/artifacts/result", headers={
                **key, "X-Compute-Lease": claim["lease_token"]},
            params={"name": "result.json", "kind": "data", "attempt": claim["attempt"]},
            content=b"{}",
        )
        settled = await client.post(f"/internal/af3/jobs/{job['id']}/result", headers=key,
                                    json={"status": "completed", "actual_gpu_minutes": 2,
                                          "attempt": claim["attempt"],
                                          "lease_token": claim["lease_token"],
                                          "artifacts": [{"id": "result", "name": "result.json",
                                                         "kind": "data"}]})
    assert uploaded.status_code == settled.status_code == 200
    assert settled.json()["status"] == "completed"
