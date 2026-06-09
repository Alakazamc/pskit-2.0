import uuid

from fastapi.testclient import TestClient

from app.db.session import init_db
from app.main import app


def test_register_login_and_tools_smoke():
    init_db()
    client = TestClient(app)
    username = "test_" + uuid.uuid4().hex[:10]

    register = client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert register.status_code == 200

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == username

    tools = client.get("/api/tools")
    assert tools.status_code == 200
    assert len(tools.json()["tools"]) >= 10

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True


def test_task_session_ownership_is_enforced():
    init_db()
    owner = TestClient(app)
    other = TestClient(app)
    owner_name = "owner_" + uuid.uuid4().hex[:10]
    other_name = "other_" + uuid.uuid4().hex[:10]

    assert owner.post("/api/auth/register", json={"username": owner_name, "password": "password123"}).status_code == 200
    assert other.post("/api/auth/register", json={"username": other_name, "password": "password123"}).status_code == 200

    session = owner.post("/api/agent/sessions", json={"title": "Owned"})
    assert session.status_code == 200
    session_id = session.json()["id"]

    created = owner.post(
        "/api/tasks",
        json={
            "task_type": "run_alphafold3",
            "session_id": session_id,
            "input": {"entities": [{"type": "protein", "sequence": "ACDEFGHIK"}]},
        },
    )
    assert created.status_code == 200
    assert created.json()["status"] == "queued"

    forbidden = other.post(
        "/api/tasks",
        json={
            "task_type": "run_alphafold3",
            "session_id": session_id,
            "input": {"entities": [{"type": "protein", "sequence": "ACDEFGHIK"}]},
        },
    )
    assert forbidden.status_code == 404
