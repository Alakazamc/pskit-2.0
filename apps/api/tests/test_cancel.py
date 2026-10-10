import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["mock", "pi"])
async def test_cancelling_af3_releases_reservation_and_never_completes(tmp_path, runtime):
    settings = Settings(
        agent_runtime=runtime, agent_db_path=str(tmp_path / "agent.sqlite3"),
        mock_af3_seconds=0.08,
    )
    app = create_app(settings, pi_runner=object() if runtime == "pi" else None)
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
            a = {"Authorization": f"Bearer {alice['access_token']}"}
            b = {"Authorization": f"Bearer {bob['access_token']}"}
            job = (await client.post("/api/v1/af3/jobs", headers=a, json={"estimated_gpu_minutes": 20})).json()
            denied = await client.delete(f"/api/v1/af3/jobs/{job['id']}", headers=b)
            cancelled = await client.delete(f"/api/v1/af3/jobs/{job['id']}", headers=a)
            repeated = await client.delete(f"/api/v1/af3/jobs/{job['id']}", headers=a)
            usage = (await client.get("/api/v1/usage", headers=a)).json()["gpu"]
            await asyncio.sleep(0.2)
            later = (await client.get(f"/api/v1/af3/jobs/{job['id']}", headers=a)).json()

    assert denied.status_code == 404
    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.json()["status"] == repeated.json()["status"] == "cancelled"
    assert usage["reserved"] == usage["used"] == 0
    assert later["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelling_waiting_run_cancels_job_and_closes_event_stream():
    app = create_app(Settings(mock_af3_seconds=0.2))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            token = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "run AF3"},
            )).json()["run_id"]
            cancelled = await client.delete(f"/api/v1/runs/{run_id}", headers=headers)
            repeated = await client.delete(f"/api/v1/runs/{run_id}", headers=headers)
            events = await asyncio.wait_for(
                client.get(f"/api/v1/runs/{run_id}/events", headers=headers), timeout=1,
            )
            usage = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]
            await asyncio.sleep(0.3)
            status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()

    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.json()["status"] == repeated.json()["status"] == status["status"] == "cancelled"
    assert usage["reserved"] == usage["used"] == 0
    assert "event: run.cancelled" in events.text
    assert "event: run.completed" not in events.text


@pytest.mark.asyncio
async def test_cancelling_active_pi_run_cannot_later_write_a_completion(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            started.set()
            await release.wait()
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "late answer"}

    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    app = create_app(settings, pi_runner=SlowPi())
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            token = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "slow research"},
            )).json()["run_id"]
            await asyncio.wait_for(started.wait(), timeout=1)
            cancelled = await client.delete(f"/api/v1/runs/{run_id}", headers=headers)
            release.set()
            await asyncio.sleep(0.05)
            status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()
            events = (await client.get(
                f"/api/v1/runs/{run_id}/events?follow=false", headers=headers,
            )).text
            messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)).json()

    assert cancelled.json()["status"] == status["status"] == "cancelled"
    assert "event: run.completed" not in events
    assert [message["role"] for message in messages] == ["user"]
