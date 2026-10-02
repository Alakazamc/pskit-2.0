from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app

FOLD_INPUT = {
    "name": "RNA complex", "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3", "version": 4,
}
WORKER_RESOURCES = {"resources": {"capabilities": ["af3"], "gpu_count": 1,
                                  "gpu_memory_mb": 49_152}}


@pytest.mark.asyncio
async def test_af3_job_declares_gpu_requirements_and_only_matching_worker_claims_it(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
        af3_min_gpu_memory_mb=40_960,
    ), pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 20, "fold_input": FOLD_INPUT})
        assert created.status_code == 200
        assert created.json()["resource_requirements"] == {
            "capability": "af3", "gpu_count": 1, "min_gpu_memory_mb": 40_960,
        }
        claim_url = "/internal/compute/af3/jobs/claim"
        key = {"X-Compute-Key": "compute-key"}
        missing = await client.post(claim_url, headers=key, json={"worker_id": "unknown"})
        small = await client.post(claim_url, headers=key, json={
            "worker_id": "small", "resources": {
                "capabilities": ["af3"], "gpu_count": 1, "gpu_memory_mb": 24_576,
            },
        })
        wrong = await client.post(claim_url, headers=key, json={
            "worker_id": "other", "resources": {
                "capabilities": ["other"], "gpu_count": 1, "gpu_memory_mb": 49_152,
            },
        })
        suitable = await client.post(claim_url, headers=key, json={
            "worker_id": "a6000", "resources": {
                "capabilities": ["af3"], "gpu_count": 1, "gpu_memory_mb": 49_152,
            },
        })
    assert missing.status_code == 422
    assert small.status_code == wrong.status_code == suitable.status_code == 200
    assert small.json() == wrong.json() == []
    assert [job["id"] for job in suitable.json()] == [created.json()["id"]]


@pytest.mark.asyncio
async def test_worker_skips_older_incompatible_job_and_claims_matching_persisted_job(tmp_path):
    db_path = str(tmp_path / "shared.sqlite3")
    high = create_app(Settings(
        agent_runtime="pi", agent_db_path=db_path, af3_executor="callback",
        compute_callback_key="compute-key", af3_min_gpu_memory_mb=80_000,
    ), pi_runner=object())
    low = create_app(Settings(
        agent_runtime="pi", agent_db_path=db_path, af3_executor="callback",
        compute_callback_key="compute-key", af3_min_gpu_memory_mb=24_000,
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=high),
                                 base_url="http://high") as first_client:
        identity = (await first_client.post("/api/v1/auth/demo",
                                            json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        older = (await first_client.post("/api/v1/af3/jobs", headers=user,
                                         json={"fold_input": FOLD_INPUT})).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=low),
                                 base_url="http://low") as second_client:
        identity = (await second_client.post("/api/v1/auth/demo",
                                             json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        newer = (await second_client.post("/api/v1/af3/jobs", headers=user,
                                          json={"fold_input": FOLD_INPUT})).json()
        claim = await second_client.post("/internal/compute/af3/jobs/claim",
                                         headers={"X-Compute-Key": "compute-key"}, json={
            "worker_id": "a6000", "max_jobs": 2,
            "resources": {"capabilities": ["af3"], "gpu_count": 1,
                          "gpu_memory_mb": 48_000},
        })
        saved_older = (await second_client.get(f"/api/v1/af3/jobs/{older['id']}",
                                               headers=user)).json()
    assert claim.status_code == 200
    assert [job["id"] for job in claim.json()] == [newer["id"]]
    assert saved_older["status"] == "queued"
    assert saved_older["resource_requirements"]["min_gpu_memory_mb"] == 80_000


@pytest.mark.asyncio
@pytest.mark.parametrize("gpu_count", [1, 2])
async def test_worker_claim_respects_reported_gpu_capacity(tmp_path, gpu_count):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        jobs = [(await client.post("/api/v1/af3/jobs", headers=user,
                                   json={"fold_input": FOLD_INPUT})).json()["id"] for _ in range(2)]
        claim_url = "/internal/compute/af3/jobs/claim"
        key = {"X-Compute-Key": "compute-key"}
        claim_body = {"worker_id": "a6000", "max_jobs": 2, "resources": {
            **WORKER_RESOURCES["resources"], "gpu_count": gpu_count,
        }}
        first = await client.post(claim_url, headers=key, json=claim_body)
        second = await client.post(claim_url, headers=key, json=claim_body)
    assert first.status_code == second.status_code == 200
    assert [job["id"] for job in first.json()] == jobs[:gpu_count]
    assert second.json() == []


@pytest.mark.asyncio
async def test_live_worker_skips_legacy_job_with_unknown_gpu_memory_requirement(tmp_path):
    from app.contracts.capabilities import Af3FoldInput
    from app.domain.persistent_conversation import PersistentConversationStore

    db_path = str(tmp_path / "shared.sqlite3")
    legacy = PersistentConversationStore(db_path).create_af3_job(
        "alice", 20, fold_input=Af3FoldInput.model_validate(FOLD_INPUT),
    )
    app = create_app(Settings(
        mode="live", agent_runtime="pi", agent_db_path=db_path,
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example.org/v1", model_gateway_model="research-model",
        model_gateway_api_key="server-key", af3_executor="callback",
        compute_callback_key="compute-key", af3_min_gpu_memory_mb=40_960,
    ), pi_runner=object())
    current = app.state.conversations.create_af3_job(
        "alice", 20, fold_input=Af3FoldInput.model_validate(FOLD_INPUT),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        claim = await client.post("/internal/compute/af3/jobs/claim",
                                  headers={"X-Compute-Key": "compute-key"}, json={
            "worker_id": "a6000", "max_jobs": 2, **WORKER_RESOURCES,
        })
    assert claim.status_code == 200
    assert [job["id"] for job in claim.json()] == [current.id]
    assert app.state.conversations.af3_job_for_compute(legacy.id).status == "queued"


@pytest.mark.asyncio
async def test_compute_worker_claims_only_one_real_input_and_reclaims_expired_lease(
    tmp_path, monkeypatch,
):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        legacy = await client.post("/api/v1/af3/jobs", headers=user,
                                   json={"estimated_gpu_minutes": 5})
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 20,
                                          "fold_input": FOLD_INPUT})
        claim_url = "/internal/compute/af3/jobs/claim"
        anonymous = await client.post(claim_url, json={"worker_id": "a6000", **WORKER_RESOURCES})
        first = await client.post(claim_url, headers={"X-Compute-Key": "compute-key"},
                                  json={"worker_id": "a6000", "lease_seconds": 60,
                                        **WORKER_RESOURCES})
        second = await client.post(claim_url, headers={"X-Compute-Key": "compute-key"},
                                   json={"worker_id": "h100", "lease_seconds": 60,
                                         **WORKER_RESOURCES})
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=61))
        reclaimed = await client.post(claim_url, headers={"X-Compute-Key": "compute-key"},
                                      json={"worker_id": "h100", "lease_seconds": 60,
                                            **WORKER_RESOURCES})
        public_job = await client.get(f"/api/v1/af3/jobs/{created.json()['id']}", headers=user)

    assert legacy.status_code == created.status_code == 200
    assert anonymous.status_code == 404
    assert first.status_code == second.status_code == reclaimed.status_code == 200
    assert [job["id"] for job in first.json()] == [created.json()["id"]]
    assert first.json()[0]["fold_input"] == FOLD_INPUT
    assert first.json()[0]["attempt"] == 1
    assert len(first.json()[0]["lease_token"]) >= 32
    assert second.json() == []
    assert [job["id"] for job in reclaimed.json()] == [created.json()["id"]]
    assert reclaimed.json()[0]["attempt"] == 2
    assert reclaimed.json()[0]["lease_token"] != first.json()[0]["lease_token"]
    assert "lease_token" not in created.json()
    assert "lease_token" not in public_job.json()


@pytest.mark.asyncio
async def test_cancelled_compute_job_is_not_reclaimed(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 20,
                                          "fold_input": FOLD_INPUT})
        claim_url = "/internal/compute/af3/jobs/claim"
        claimed = await client.post(claim_url, headers={"X-Compute-Key": "compute-key"},
                                    json={"worker_id": "a6000", "lease_seconds": 60,
                                          **WORKER_RESOURCES})
        cancelled = await client.delete(f"/api/v1/af3/jobs/{created.json()['id']}",
                                        headers=user)
        retried = await client.post(claim_url, headers={"X-Compute-Key": "compute-key"},
                                    json={"worker_id": "h100", "lease_seconds": 60,
                                          **WORKER_RESOURCES})
    assert claimed.status_code == 200 and len(claimed.json()) == 1
    assert cancelled.json()["status"] == "cancelled"
    assert retried.json() == []


@pytest.mark.asyncio
async def test_compute_heartbeat_extends_only_the_claiming_workers_lease(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 20,
                                          "fold_input": FOLD_INPUT})
        job_id = created.json()["id"]
        key = {"X-Compute-Key": "compute-key"}
        claim = (await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                   json={"worker_id": "a6000", "lease_seconds": 60,
                                         **WORKER_RESOURCES})).json()[0]
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=30))
        wrong = await client.post(f"/internal/compute/af3/jobs/{job_id}/heartbeat",
                                  headers=key, json={"worker_id": "h100", "lease_seconds": 60,
                                                     "lease_token": claim["lease_token"]})
        wrong_token = await client.post(f"/internal/compute/af3/jobs/{job_id}/heartbeat",
                                        headers=key, json={"worker_id": "a6000", "lease_seconds": 60,
                                                           "lease_token": "invalid"})
        renewed = await client.post(f"/internal/compute/af3/jobs/{job_id}/heartbeat",
                                    headers=key, json={"worker_id": "a6000", "lease_seconds": 60,
                                                       "lease_token": claim["lease_token"]})
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=61))
        not_reclaimed = await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                          json={"worker_id": "h100", "lease_seconds": 60,
                                                **WORKER_RESOURCES})
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=91))
        reclaimed = await client.post("/internal/compute/af3/jobs/claim", headers=key,
                                      json={"worker_id": "h100", "lease_seconds": 60,
                                            **WORKER_RESOURCES})
    assert wrong.status_code == 409
    assert wrong_token.status_code == 409
    assert renewed.status_code == 200
    assert not_reclaimed.json() == []
    assert [job["id"] for job in reclaimed.json()] == [job_id]


@pytest.mark.asyncio
async def test_reclaimed_job_rejects_stale_results_and_artifacts(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              af3_executor="callback", compute_callback_key="compute-key"),
                     pi_runner=object())
    key = {"X-Compute-Key": "compute-key"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        identity = (await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {identity['access_token']}"}
        created = await client.post("/api/v1/af3/jobs", headers=user,
                                    json={"estimated_gpu_minutes": 20,
                                          "fold_input": FOLD_INPUT})
        job_id = created.json()["id"]
        claim_url = "/internal/compute/af3/jobs/claim"
        first = (await client.post(claim_url, headers=key,
                                   json={"worker_id": "a6000", "lease_seconds": 60,
                                         **WORKER_RESOURCES})).json()[0]
        monkeypatch.setattr(persistence, "_now", lambda: now + timedelta(seconds=61))
        second = (await client.post(claim_url, headers=key,
                                    json={"worker_id": "h100", "lease_seconds": 60,
                                          **WORKER_RESOURCES})).json()[0]
        stale_upload = await client.put(
            f"/internal/af3/jobs/{job_id}/artifacts/structure-1",
            headers={**key, "X-Compute-Lease": first["lease_token"]},
            params={"name": "prediction.cif", "kind": "structure", "attempt": second["attempt"]},
            content=b"old-worker",
        )
        stale_result = await client.post(
            f"/internal/af3/jobs/{job_id}/result", headers=key,
            json={"status": "completed", "actual_gpu_minutes": 12,
                  "attempt": second["attempt"], "lease_token": first["lease_token"]},
        )
        missing_attempt = await client.post(
            f"/internal/af3/jobs/{job_id}/result", headers=key,
            json={"status": "completed", "actual_gpu_minutes": 12},
        )
        current_result = await client.post(
            f"/internal/af3/jobs/{job_id}/result", headers=key,
            json={"status": "completed", "actual_gpu_minutes": 12,
                  "attempt": second["attempt"], "lease_token": second["lease_token"]},
        )
        duplicate = await client.post(
            f"/internal/af3/jobs/{job_id}/result", headers=key,
            json={"status": "completed", "actual_gpu_minutes": 12,
                  "attempt": second["attempt"], "lease_token": second["lease_token"]},
        )
        usage = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]
    assert first["attempt"] == 1 and second["attempt"] == 2
    assert stale_upload.status_code == stale_result.status_code == missing_attempt.status_code == 409
    assert current_result.status_code == duplicate.status_code == 200
    assert usage["used"] == 12 and usage["reserved"] == 0
