import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_pi_plan_updates_are_authorized_persisted_and_replayable(tmp_path):
    class PlanningPi:
        environment = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            env = kwargs["environment"]
            self.environment = env
            headers = {"Authorization": f"Bearer {env['PSKIT_AGENT_TOOL_TOKEN']}"}
            url = f"/internal/runs/{env['PSKIT_RUN_ID']}/plan"
            first = [{"id": "inspect", "title": "Inspect sequences", "status": "in_progress"}]
            second = [{"id": "inspect", "title": "Inspect sequences", "status": "completed"},
                      {"id": "report", "title": "Write report", "status": "pending"}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                denied = await client.post(url, json={"steps": first})
                assert denied.status_code == 401
                created = await client.post(url, headers=headers, json={"steps": first})
                repeated = await client.post(url, headers=headers, json={"steps": first})
                updated = await client.post(url, headers=headers, json={"steps": second})
                invalid = await client.post(url, headers=headers, json={"steps": second + second})
                assert [created.status_code, repeated.status_code, updated.status_code,
                        invalid.status_code] == [200, 200, 200, 422]
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Done"}

    runner = PlanningPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=runner)
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            session_id = f"session-{login['user']['id']}"
            response = await client.post(f"/api/v1/c/{session_id}/messages",
                                         headers=headers, json={"content": "Analyze"})
            run_id = response.json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            assert status == "completed"
            stream = (await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                       headers=headers)).text
            after = await client.post(f"/internal/runs/{run_id}/plan", headers={
                "Authorization": f"Bearer {runner.environment['PSKIT_AGENT_TOOL_TOKEN']}",
            }, json={
                "steps": [{"id": "late", "title": "Late write", "status": "pending"}],
            })
            assert after.status_code == 409

    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    plans = [event for event in events if event["type"].startswith("plan.")]
    assert [event["type"] for event in plans] == ["plan.created", "plan.updated"]
    assert plans[-1]["data"]["steps"][1] == {
        "id": "report", "title": "Write report", "status": "pending",
    }
    assert "PSKIT_AGENT_TOOL_TOKEN" not in stream
