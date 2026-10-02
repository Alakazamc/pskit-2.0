import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_user_can_create_a_project_and_session_without_affecting_another_user():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        created_project = await client.post("/api/v1/g", headers=alice_headers,
                                            json={"name": "RNA Design", "description": "候选结构"})
        assert created_project.status_code == 201
        project = created_project.json()
        project_path = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}"
        created_session = await client.post(
            f"{project_path}/c", headers=alice_headers,
            json={"title": "第一个实验"},
        )
        assert created_session.status_code == 201
        session = created_session.json()
        alice_projects = (await client.get("/api/v1/g", headers=alice_headers)).json()
        alice_sessions = (await client.get(
            f"{project_path}/c", headers=alice_headers
        )).json()
        bob_project = await client.get(
            f"{project_path}/c", headers=bob_headers
        )
        bob_session = await client.get(
            f"{project_path}/c/{session['id']}/messages", headers=bob_headers
        )

    assert any(item["id"] == project["id"] for item in alice_projects)
    assert alice_sessions == [session]
    assert session["project_id"] == project["id"]
    assert bob_project.status_code == 404
    assert bob_session.status_code == 404


@pytest.mark.asyncio
async def test_created_projects_and_sessions_survive_restart_in_pi_mode(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project = (await client.post("/api/v1/g", headers=headers,
                                     json={"name": "RNA Design"})).json()
        project_path = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}"
        session = (await client.post(
            f"{project_path}/c", headers=headers,
            json={"title": "第一个实验"},
        )).json()

    second = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        projects = (await client.get("/api/v1/g", headers=headers)).json()
        sessions = (await client.get(
            f"{project_path}/c", headers=headers
        )).json()

    assert project in projects
    assert sessions == [session]


@pytest.mark.parametrize("runtime", ["mock", "pi"])
@pytest.mark.asyncio
async def test_project_icon_can_be_selected_changed_and_is_private(runtime, tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")) if runtime == "pi" else None
    app = create_app(settings, pi_runner=object()) if settings else create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        created = await client.post("/api/v1/g", headers=a,
                                    json={"name": "蛋白研究", "icon": "dna"})
        assert created.status_code == 201
        project = created.json()
        assert project["icon"] == "dna"
        path = f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/icon"
        assert (await client.patch(path, headers=b, json={"icon": "atom"})).status_code == 404
        assert (await client.patch(path, headers=a, json={"icon": "unknown"})).status_code == 422
        changed = await client.patch(path, headers=a, json={"icon": "microscope"})
        assert changed.status_code == 200
        assert changed.json()["icon"] == "microscope"
        assert changed.json() in (await client.get("/api/v1/g", headers=a)).json()
        assert project not in (await client.get("/api/v1/g", headers=b)).json()
    if settings:
        restarted = create_app(settings, pi_runner=object())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
            login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            assert changed.json() in (await client.get("/api/v1/g", headers=headers)).json()


@pytest.mark.parametrize("runtime", ["mock", "pi"])
@pytest.mark.asyncio
async def test_session_url_can_resolve_current_project_after_a_move(runtime, tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")) if runtime == "pi" else None
    app = create_app(settings, pi_runner=object()) if settings else create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        personal_id = (await client.get("/api/v1/g", headers=a)).json()[0]["id"]
        target = (await client.post("/api/v1/g", headers=a, json={"name": "实验"})).json()
        session = (await client.post("/api/v1/c", headers=a,
                                     json={"title": "P53"})).json()
        path = f"/api/v1/c/{session['id']}"
        assert (await client.get(path, headers=a)).json()["project_id"] == personal_id
        assert (await client.get(path, headers=b)).status_code == 404
        moved = await client.patch(f"{path}/project", headers=a,
                                   json={"project_id": target["id"]})
        assert moved.status_code == 200
        moved_path = f"/api/v1/g/g-p-{target['id'].removeprefix('project-')}/c/{session['id']}"
        assert (await client.get(path, headers=a)).status_code == 404
        assert (await client.get(moved_path, headers=a)).json()["project_id"] == target["id"]
        assert (await client.delete(moved_path, headers=a)).status_code == 204
        assert (await client.get(moved_path, headers=a)).status_code == 404


@pytest.mark.asyncio
async def test_user_can_rename_and_archive_owned_workspace_items():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        project = (await client.post("/api/v1/g", headers=a,
                                     json={"name": "Draft"})).json()
        project_path = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}"
        session = (await client.post(f"{project_path}/c", headers=a,
                                     json={"title": "Draft run"})).json()
        foreign = await client.patch(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}", headers=b,
                                     json={"name": "Stolen"})
        renamed_project = await client.patch(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}", headers=a,
                                              json={"name": "RNA Design"})
        renamed_session = await client.patch(f"{project_path}/c/{session['id']}", headers=a,
                                              json={"title": "Experiment 1"})
        projects = (await client.get("/api/v1/g", headers=a)).json()
        sessions = (await client.get(f"{project_path}/c", headers=a)).json()
        archived_session = await client.delete(f"{project_path}/c/{session['id']}", headers=a)
        hidden_session = await client.get(f"{project_path}/c/{session['id']}/messages", headers=a)
        archived_project = await client.delete(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}", headers=a)
        hidden_project = await client.get(f"{project_path}/c", headers=a)

    assert foreign.status_code == 404
    assert renamed_project.status_code == renamed_session.status_code == 200
    assert renamed_project.json()["name"] == "RNA Design"
    assert renamed_session.json()["title"] == "Experiment 1"
    assert renamed_project.json() in projects
    assert renamed_session.json() in sessions
    assert archived_session.status_code == archived_project.status_code == 204
    assert hidden_session.status_code == hidden_project.status_code == 404


@pytest.mark.asyncio
async def test_personal_chat_can_move_to_an_owned_project_without_losing_messages():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        (await client.get("/api/v1/g", headers=a)).json()[0]
        target = (await client.post("/api/v1/g", headers=a, json={"name": "蛋白设计"})).json()
        session = (await client.post("/api/v1/c", headers=a,
                                     json={"title": "P53 结构"})).json()
        sent = await client.post(f"/api/v1/c/{session['id']}/messages", headers=a,
                                 json={"content": "分析 P53", "skills": [], "resources": [], "attachments": []})
        foreign = await client.patch(f"/api/v1/c/{session['id']}/project", headers=b,
                                      json={"project_id": target["id"]})
        moved = await client.patch(f"/api/v1/c/{session['id']}/project", headers=a,
                                    json={"project_id": target["id"]})
        source_sessions = (await client.get("/api/v1/c", headers=a)).json()
        target_path = f"/api/v1/g/g-p-{target['id'].removeprefix('project-')}/c"
        target_sessions = (await client.get(target_path, headers=a)).json()
        messages = (await client.get(f"{target_path}/{session['id']}/messages", headers=a)).json()

    assert sent.status_code == 200
    assert foreign.status_code == 404
    assert moved.status_code == 200
    assert moved.json()["project_id"] == target["id"]
    assert not any(item["id"] == session["id"] for item in source_sessions)
    assert any(item["id"] == session["id"] for item in target_sessions)
    assert any(part.get("text") == "分析 P53" for message in messages for part in message["parts"])


@pytest.mark.asyncio
async def test_project_skill_settings_survive_restart_and_apply_defaults(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project = (await client.post("/api/v1/g", headers=headers,
                                     json={"name": "蛋白设计"})).json()
        configured = await client.put(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/skills", headers=headers,
                                       json={"skill_ids": ["structure-review"],
                                             "default_skill_ids": ["structure-review"]})
        invalid = await client.put(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/skills", headers=headers,
                                    json={"skill_ids": ["unknown"], "default_skill_ids": ["unknown"]})
        too_many_defaults = await client.put(
            f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/skills", headers=headers,
            json={"skill_ids": ["structure-review"],
                  "default_skill_ids": ["structure-review"] * 4},
        )
    second = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        saved = await client.get(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/skills", headers=headers)

    assert configured.status_code == 200
    assert configured.json() == {"skill_ids": ["structure-review"], "default_skill_ids": ["structure-review"]}
    assert invalid.status_code == 422
    assert too_many_defaults.status_code == 422
    assert saved.json() == configured.json()


@pytest.mark.asyncio
async def test_project_default_skills_are_applied_to_new_messages():
    app = create_app()
    seen_skills = []
    resolve = app.state.catalog.resolve_context

    def capture(user_id, payload):
        seen_skills.extend(ref.id for ref in payload.skills)
        return resolve(user_id, payload)

    app.state.catalog.resolve_context = capture
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        project = (await client.post("/api/v1/g", headers=headers,
                                     json={"name": "蛋白设计"})).json()
        settings = await client.put(f"/api/v1/g/g-p-{(project['id']).removeprefix('project-')}/skills", headers=headers,
                                    json={"skill_ids": ["structure-review"],
                                          "default_skill_ids": ["structure-review"]})
        project_path = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}"
        session = (await client.post(f"{project_path}/c", headers=headers,
                                     json={"title": "结构分析"})).json()
        sent = await client.post(f"{project_path}/c/{session['id']}/messages", headers=headers,
                                 json={"content": "分析 P53", "skills": [], "resources": [], "attachments": []})
    assert settings.status_code == 200
    assert sent.status_code == 200
    assert seen_skills == ["structure-review"]
