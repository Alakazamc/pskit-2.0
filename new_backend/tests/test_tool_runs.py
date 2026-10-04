import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("runtime", ["mock", "pi"])
@pytest.mark.asyncio
async def test_tool_history_filters_by_exact_tool_and_current_owner(runtime, tmp_path):
    settings = Settings(agent_runtime=runtime, agent_db_path=str(tmp_path / "agent.sqlite3"))
    app = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        headers = {"Authorization": f"Bearer {alice['access_token']}"}
        first = app.state.tool_runs.add(alice["user"]["id"], "lab/search", {"query": "p53"}, {})
        app.state.tool_runs.add(alice["user"]["id"], "lab/search_extra", {}, {})
        latest = app.state.tool_runs.add(alice["user"]["id"], "lab/search", {"query": "rna"}, {})
        app.state.tool_runs.add(bob["user"]["id"], "lab/search", {}, {"private": True})
        response = await client.get("/api/v1/tool-runs", headers=headers, params={"tool": "lab/search"})
        assert response.status_code == 200
        assert [run["id"] for run in response.json()] == [latest.id, first.id]
        assert (await client.get("/api/v1/tool-runs", headers=headers,
                                 params={"tool": "missing"})).json() == []
        assert len((await client.get("/api/v1/tool-runs", headers=headers)).json()) == 3


@pytest.mark.asyncio
async def test_mcp_run_starts_personal_and_can_be_saved_to_owned_project():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        project = (await client.post("/api/v1/g", headers=a, json={"name": "蛋白设计"})).json()
        invoked = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=a,
                                    json={"query": "p53"})
        run_id = invoked.json()["run_id"]
        personal = (await client.get("/api/v1/tool-runs", headers=a)).json()
        foreign_list = (await client.get("/api/v1/tool-runs", headers=b)).json()
        foreign_move = await client.patch(f"/api/v1/tool-runs/{run_id}/project", headers=b,
                                          json={"project_id": project["id"]})
        saved = await client.patch(f"/api/v1/tool-runs/{run_id}/project", headers=a,
                                   json={"project_id": project["id"]})

    assert invoked.status_code == 200
    assert personal[0]["project_id"] is None
    assert personal[0]["result"] == invoked.json()["result"]
    assert foreign_list == [] and foreign_move.status_code == 404
    assert saved.status_code == 200 and saved.json()["project_id"] == project["id"]


@pytest.mark.asyncio
async def test_tool_run_assignment_survives_restart_in_pi_mode(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=object())
    # The Pi runtime may disable direct MCP invocation; store a completed provider result directly.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project = (await client.post("/api/v1/g", headers=headers, json={"name": "蛋白设计"})).json()
        run = first.state.tool_runs.add(login["user"]["id"], "search_pdb", {"query": "p53"}, {"hits": []})
        saved = await client.patch(f"/api/v1/tool-runs/{run.id}/project", headers=headers,
                                   json={"project_id": project["id"]})
    second = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        runs = (await client.get("/api/v1/tool-runs", headers=headers)).json()
    assert saved.status_code == 200
    assert runs[0]["id"] == run.id and runs[0]["project_id"] == project["id"]
