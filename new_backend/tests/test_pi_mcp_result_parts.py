import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_completed_pi_mcp_result_is_saved_as_a_message_part_from_the_server_ledger(tmp_path):
    class CallingPi:
        app = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            environment = kwargs["environment"]
            run_id = environment["PSKIT_RUN_ID"]
            await on_event({
                "type": "tool_execution_start", "toolCallId": "call-search", "toolName": "search_pdb",
            })
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://internal",
            ) as internal:
                result = await internal.post(
                    "/internal/mcp/tools/search_pdb/invoke",
                    headers={"Authorization": f"Bearer {environment['PSKIT_AGENT_TOOL_TOKEN']}"},
                    json={"run_id": run_id, "tool_call_id": "call-search", "arguments": {"query": "RNA"}},
                )
            assert result.status_code == 200
            await on_event({
                "type": "tool_execution_end", "toolCallId": "call-search", "toolName": "search_pdb",
                "result": {"content": [{"type": "text", "text": '{"forged":"do not trust Pi output"}'}]},
            })
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Found one structure"}

    pi = CallingPi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")), pi_runner=pi,
    )
    pi.app = app
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = project_id.replace("project-", "session-")
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "Find a structure", "resources": [{"id": "search_pdb", "name": "PDB"}]},
            )
            run_id = sent.json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            assert status == "completed"
            messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)).json()

    parts = messages[-1]["parts"]
    results = [part for part in parts if part["type"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call-search"
    assert results[0]["tool"] == "search_pdb"
    assert results[0]["result"]["hits"][0]["pdb_id"] == "1A9N"
    assert "forged" not in results[0]["result"]
