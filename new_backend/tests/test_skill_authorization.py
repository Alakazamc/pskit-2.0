import httpx
import pytest

from app.config import Settings
from app.main import create_app


class UnusedPi:
    async def prompt(self, *args, **kwargs):
        raise AssertionError("Unauthorized Skill must be rejected before Pi runs")


async def login(client: httpx.AsyncClient, email: str):
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    return response.json()["user"]["id"], {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


@pytest.mark.asyncio
async def test_admin_skill_grants_filter_catalog_and_message_context_after_restart(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                        admin_api_key="admin-secret")
    first = create_app(settings, pi_runner=UnusedPi())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first), base_url="http://test",
    ) as client:
        alice_id, alice = await login(client, "alice@example.org")
        _, bob = await login(client, "bob@example.org")
        assert (await client.get("/api/v1/skills", headers=alice)).json()
        updated = await client.put(
            f"/api/v1/admin/users/{alice_id}/skills",
            headers={"X-Admin-Key": "admin-secret"}, json={"skill_ids": []},
        )
        assert updated.status_code == 200
        assert (await client.get("/api/v1/skills", headers=alice)).json() == []
        assert (await client.get("/api/v1/skills", headers=bob)).json()
        assert (await client.get("/api/v1/resources", headers=alice)).json() == []
        assert (await client.get("/api/v1/mcp/tools", headers=alice)).json() == []
        denied_tool = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=alice, json={"query": "p53"},
        )
        assert denied_tool.status_code == 403
        project_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
        rejected_project = await client.put(
            f"/api/v1/g/g-p-{(project_id).removeprefix('project-')}/skills", headers=alice,
            json={"skill_ids": ["structure-review"], "default_skill_ids": []},
        )
        assert rejected_project.status_code == 422
        session_id = project_id.replace("project-", "session-")
        rejected_message = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=alice,
            json={"content": "Review", "skills": [{"id": "structure-review", "name": "Review"}]},
        )
        assert rejected_message.status_code == 404
        assert rejected_message.json()["detail"]["code"] == "CONTEXT_NOT_FOUND"

    restarted = create_app(settings, pi_runner=UnusedPi())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://test",
    ) as client:
        _, alice = await login(client, "alice@example.org")
        assert (await client.get("/api/v1/skills", headers=alice)).json() == []
