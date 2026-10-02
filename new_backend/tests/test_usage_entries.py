import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_usage_entries_show_token_changes_and_gpu_jobs_by_owner(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        session_id = f"session-{alice['user']['id']}"
        sent = await client.post(f"/api/v1/c/{session_id}/messages", headers=a,
                                 json={"content": "Analyze"})
        job = await client.post("/api/v1/af3/jobs", headers=a,
                                json={"estimated_gpu_minutes": 20})
        app.state.conversations.adjust_tokens(alice["user"]["id"], 3,
                                               run_id=sent.json()["run_id"])
        before = (await client.get("/api/v1/usage/entries", headers=a)).json()
        foreign = (await client.get("/api/v1/usage/entries", headers=b)).json()
        app.state.conversations.settle_af3_job(job.json()["id"], "completed", 18, [])
        after = (await client.get("/api/v1/usage/entries", headers=a)).json()

    assert sent.status_code == 200 and job.status_code == 200
    assert foreign == []
    token_entries = [item for item in before if item["resource"] == "tokens"]
    assert [item["kind"] for item in token_entries] == ["reservation", "adjustment"]
    assert [item["amount"] for item in token_entries] == [max(1, len("Analyze") // 4), 3]
    assert all(item["run_id"] == sent.json()["run_id"] for item in token_entries)
    gpu_before = next(item for item in before if item["resource"] == "gpu_minutes")
    gpu_after = next(item for item in after if item["resource"] == "gpu_minutes")
    assert (gpu_before["amount"], gpu_before["status"]) == (20, "queued")
    assert (gpu_after["amount"], gpu_after["status"]) == (18, "completed")
    assert gpu_after["job_id"] == job.json()["id"]


@pytest.mark.asyncio
async def test_mock_usage_entries_report_real_python_quota_changes_by_owner():
    app = create_app(Settings(agent_runtime="mock", mock_af3_seconds=3600))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        session_id = f"session-{alice['user']['id']}"
        sent = await client.post(f"/api/v1/c/{session_id}/messages", headers=a,
                                 json={"content": "Analyze"})
        job = await client.post("/api/v1/af3/jobs", headers=a,
                                json={"estimated_gpu_minutes": 20})
        before = (await client.get("/api/v1/usage/entries", headers=a)).json()
        foreign = (await client.get("/api/v1/usage/entries", headers=b)).json()
        cancelled = await client.delete(f"/api/v1/af3/jobs/{job.json()['id']}", headers=a)
        after = (await client.get("/api/v1/usage/entries", headers=a)).json()

    assert sent.status_code == 200 and job.status_code == 200 and cancelled.status_code == 200
    assert foreign == []
    token = next(item for item in before if item["resource"] == "tokens")
    gpu_before = next(item for item in before if item["resource"] == "gpu_minutes")
    gpu_after = next(item for item in after if item["resource"] == "gpu_minutes")
    assert (token["kind"], token["amount"], token["status"]) == ("charge", 1, "posted")
    assert len(token["period"]) == 7
    assert (gpu_before["kind"], gpu_before["amount"], gpu_before["status"]) == ("job", 20, "queued")
    assert gpu_before["job_id"] == job.json()["id"]
    assert (gpu_after["amount"], gpu_after["status"]) == (0, "cancelled")


@pytest.mark.asyncio
async def test_mock_usage_entries_update_settled_gpu_minutes():
    app = create_app(Settings(agent_runtime="mock", mock_af3_seconds=3600))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        job = await client.post("/api/v1/af3/jobs", headers=headers,
                                json={"estimated_gpu_minutes": 20})
        app.state.af3.advance(0)
        app.state.af3.advance(0)
        entries = (await client.get("/api/v1/usage/entries", headers=headers)).json()
        usage = (await client.get("/api/v1/usage", headers=headers)).json()

    assert job.status_code == 200
    gpu = next(item for item in entries if item["resource"] == "gpu_minutes")
    assert (gpu["amount"], gpu["status"], gpu["job_id"]) == (18, "completed", job.json()["id"])
    assert (usage["gpu"]["used"], usage["gpu"]["reserved"]) == (18, 0)
