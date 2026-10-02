import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_personal_and_project_chats_use_scoped_chatgpt_style_api_routes():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project = (await client.post("/api/v1/g", headers=headers, json={"name": "OS"})).json()
        key = f"g-p-{project['id'].removeprefix('project-')}"
        assert key.endswith("-os")

        personal = (await client.post("/api/v1/c", headers=headers, json={"title": "General"})).json()
        project_chat = (await client.post(f"/api/v1/g/{key}/c", headers=headers,
                                          json={"title": "Project work"})).json()
        assert (await client.get(f"/api/v1/c/{personal['id']}", headers=headers)).status_code == 200
        assert (await client.get(f"/api/v1/g/{key}/c/{project_chat['id']}", headers=headers)).status_code == 200
        assert (await client.get(f"/api/v1/c/{project_chat['id']}", headers=headers)).status_code == 404
        assert (await client.get(f"/api/v1/g/{key}/c/{personal['id']}", headers=headers)).status_code == 404
        assert (await client.get("/api/v1/projects", headers=headers)).status_code == 404
        assert (await client.get(f"/api/v1/sessions/{personal['id']}", headers=headers)).status_code == 404
