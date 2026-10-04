"""Each chat turn has an independent, server-enforced ten-file allowance."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("runtime", ["mock", "pi"])
@pytest.mark.parametrize("project_chat", [False, True])
@pytest.mark.asyncio
async def test_eleven_attachments_are_rejected_before_message_creation_or_token_charge(
    runtime, project_chat, tmp_path,
):
    settings = Settings(agent_runtime=runtime, agent_db_path=str(tmp_path / "agent.sqlite3"))
    app = create_app(settings, pi_runner=object() if runtime == "pi" else None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend",
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        if project_chat:
            project = (await client.post("/api/v1/g", headers=headers,
                                         json={"name": "Research"})).json()
            scope = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}/c"
        else:
            scope = "/api/v1/c"
        session = (await client.post(scope, headers=headers, json={"title": "Files"})).json()
        path = f"{scope}/{session['id']}/messages"
        attachments = []
        for index in range(11):
            name = f"notes-{index + 1}.txt"
            uploaded = await client.put("/api/v1/files/content", headers=headers,
                                        params={"name": name}, content=b"notes")
            assert uploaded.status_code == 200
            attachments.append({"id": uploaded.json()["id"], "name": name})
        before = (await client.get("/api/v1/usage", headers=headers)).json()

        rejected = await client.post(path, headers=headers, json={
            "content": "Read these", "attachments": attachments,
        })

        assert rejected.status_code == 422
        assert rejected.json()["detail"] == {"code": "TOO_MANY_ATTACHMENTS"}
        assert (await client.get(path, headers=headers)).json() == []
        assert (await client.get("/api/v1/usage", headers=headers)).json() == before


@pytest.mark.parametrize("payload", [
    {"content": None}, {"content": "Review", "attachments": "invalid"},
])
@pytest.mark.asyncio
async def test_other_message_validation_errors_keep_their_standard_response(payload):
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend",
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        session = (await client.post("/api/v1/c", headers=headers,
                                     json={"title": "Validation"})).json()
        response = await client.post(f"/api/v1/c/{session['id']}/messages", headers=headers,
                                     json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
