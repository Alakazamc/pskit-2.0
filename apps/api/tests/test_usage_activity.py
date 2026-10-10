from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
async def test_activity_fills_days_and_counts_actual_usage_not_holds_or_last_100_entries(tmp_path, persistent):
    app = create_app(Settings(agent_runtime="pi" if persistent else "mock", agent_db_path=str(tmp_path / "activity.sqlite3")), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        uid = alice["user"]["id"]
        for _ in range(105):
            app.state.quotas.charge_tokens(uid, 10)
        app.state.quotas.charge_tokens(bob["user"]["id"], 9000)
        if persistent:
            store = app.state.conversations
            run = store.send_message(uid, "session-" + uid, MessageRequest(content="Hold"))
            store.reserve_resume_tokens(uid, run.run_id, 500)
            store.record_model_attempt(uid, run.run_id, 7, "error")
            store.record_model_attempt(uid, run.run_id, 11, "completed")
            store.reserve_resume_tokens(uid, run.run_id, 500)
        headers = {"Authorization": f"Bearer {alice['access_token']}"}
        response = await client.get("/api/v1/usage/activity?days=365", headers=headers)
        assert response.status_code == 200
        activity = response.json()
        assert activity["timezone"] == "UTC"
        assert len(activity["days"]) == 365
        assert activity["days"][-1]["date"] == datetime.now(UTC).date().isoformat()
        assert activity["days"][-1]["tokens"] == 1050 + (18 if persistent else 0)
        assert all(day["tokens"] == day["gpu_ms"] == 0 for day in activity["days"][:-1])
        other = (await client.get("/api/v1/usage/activity?days=1", headers={"Authorization": f"Bearer {bob['access_token']}"})).json()
        assert other["days"][0]["tokens"] == 9000
        assert (await client.get("/api/v1/usage/activity")).status_code == 401
        for days in (0, 367):
            assert (await client.get(f"/api/v1/usage/activity?days={days}", headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_activity_gpu_only_counts_settled_minutes():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        uid = alice["user"]["id"]
        app.state.quotas.reserve_gpu(uid, "done", 20)
        app.state.quotas.reserve_gpu(uid, "waiting", 20)
        app.state.quotas.settle_gpu(uid, "done", 13)
        response = await client.get("/api/v1/usage/activity?days=1", headers={"Authorization": f"Bearer {alice['access_token']}"})
        assert response.status_code == 200
        assert response.json()["days"][0]["gpu_ms"] == 13 * 60_000
