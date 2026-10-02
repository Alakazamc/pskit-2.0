import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_persisted_queued_run_starts_after_python_restart(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))

    class FakePi:
        calls = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.calls += 1
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "恢复完成"}

    original = FakePi()
    first = create_app(settings, pi_runner=original)
    first.state.agent_service.schedule = lambda *_args: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        sent = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=headers,
            json={"content": "分析蛋白结构"},
        )
        assert sent.status_code == 200
        run_id = sent.json()["run_id"]
        assert (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"] == "queued"

    resumed = FakePi()
    second = create_app(settings, pi_runner=resumed)
    async with second.router.lifespan_context(second):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {(await client.post('/api/v1/auth/demo', json={'email': 'alice@example.org'})).json()['access_token']}"}
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)).json()

    assert status == "completed"
    assert resumed.calls == 1
    assert [message["parts"][0]["text"] for message in messages] == ["分析蛋白结构", "恢复完成"]
