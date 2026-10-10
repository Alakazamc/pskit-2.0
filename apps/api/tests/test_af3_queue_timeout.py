import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_unclaimed_af3_job_times_out_without_gpu_charge_or_agent_wakeup(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)

    class SubmitPi:
        prompts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.prompts += 1
            env = kwargs["environment"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                response = await client.post("/internal/af3/jobs", headers={
                    "Authorization": f"Bearer {env['PSKIT_AGENT_TOOL_TOKEN']}",
                }, json={"run_id": env["PSKIT_RUN_ID"], "tool_call_id": "call-af3",
                         "estimated_gpu_minutes": 20, "fold_input": {
                             "name": "small protein", "modelSeeds": [1],
                             "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
                             "dialect": "alphafold3", "version": 4,
                         }})
            assert response.status_code == 200, response.text
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": ""}

    runner = SubmitPi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
        af3_queue_timeout_seconds=60,
    ), pi_runner=runner)
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            identity = (await client.post("/api/v1/auth/demo",
                                          json={"email": "alice@example.org"})).json()
            user = {"Authorization": f"Bearer {identity['access_token']}"}
            session_id = f"session-{identity['user']['id']}"
            sent = await client.post(f"/api/v1/c/{session_id}/messages",
                                     headers=user, json={"content": "Predict this structure"})
            run_id = sent.json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=user)).json()["status"]
                if status == "waiting":
                    break
                await asyncio.sleep(0.01)
            assert status == "waiting"
            jobs = app.state.conversations.active_job_ids_for_run(identity["user"]["id"], run_id)
            assert len(jobs) == 1
            before = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]
            assert before["reserved"] == 20

            now += timedelta(seconds=61)
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=user)).json()["status"]
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
            after = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]
            stream = (await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                       headers=user)).text
            late = await client.post(f"/internal/af3/jobs/{jobs[0]}/result",
                                     headers={"X-Compute-Key": "compute-key"}, json={
                                         "status": "completed", "actual_gpu_minutes": 10,
                                     })
            stream_after = (await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                             headers=user)).text
            claim = await client.post("/internal/compute/af3/jobs/claim",
                                      headers={"X-Compute-Key": "compute-key"},
                                      json={"worker_id": "a6000", "resources": {
                                          "capabilities": ["af3"], "gpu_count": 1,
                                          "gpu_memory_mb": 49_152,
                                      }})

    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    assert status == "failed"
    assert after["reserved"] == 0 and after["used"] == 0
    assert [event["data"]["code"] for event in events if event["type"] == "run.failed"] == [
        "AF3_COMPUTE_UNAVAILABLE",
    ]
    assert late.json()["status"] == "failed" and claim.json() == []
    assert stream_after == stream and runner.prompts == 1
