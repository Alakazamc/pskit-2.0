import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_repeated_message_key_returns_the_original_run_without_double_charge(tmp_path):
    release = asyncio.Event()

    class WaitingPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await release.wait()
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "done"}

    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=WaitingPi(),
    )
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            user = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {user['access_token']}", "Idempotency-Key": "send-001"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            path = f"/api/v1/c/{session_id}/messages"
            first = await client.post(path, headers=headers, json={"content": "research question"})
            repeated = await client.post(path, headers=headers, json={"content": "research question"})
            conflict = await client.post(path, headers=headers, json={"content": "different question"})
            messages = (await client.get(path, headers=headers)).json()
            usage = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]
            release.set()

    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert [message["role"] for message in messages] == ["user"]
    assert usage["used"] == max(1, len("research question") // 4)


@pytest.mark.asyncio
async def test_mock_af3_retry_returns_original_run_after_reservation_uses_last_gpu_minutes():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        user = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {user['access_token']}", "Idempotency-Key": "af3-001"}
        app.state.quotas.set_gpu_limit(user["user"]["id"], 20)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        path = f"/api/v1/c/{session_id}/messages"
        first = await client.post(path, headers=headers, json={"content": "Run AF3"})
        repeated = await client.post(path, headers=headers, json={"content": "Run AF3"})
        usage = (await client.get("/api/v1/usage", headers=headers)).json()

    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    assert usage["gpu"]["reserved"] == 20
    assert usage["tokens"]["used"] == max(1, len("Run AF3") // 4)
