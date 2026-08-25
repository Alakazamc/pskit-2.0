from __future__ import annotations

import uuid

from app.db.session import init_db
from app.main import app
from fastapi.testclient import TestClient


def main() -> None:
    init_db()
    client = TestClient(app)
    username = "smoke_" + uuid.uuid4().hex[:10]
    password = "password123"

    register = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert register.status_code == 200, register.text
    assert register.json()["username"] == username

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text

    session = client.post("/api/agent/sessions", json={"title": "Smoke"})
    assert session.status_code == 200, session.text
    session_id = session.json()["id"]

    tools = client.get("/api/tools")
    assert tools.status_code == 200, tools.text
    assert tools.json()["tools"], "tool catalog is empty"

    task = client.post(
        "/api/tasks",
        json={
            "task_type": "run_alphafold3",
            "session_id": session_id,
            "input": {
                "entities": [{"type": "protein", "sequence": "ACDEFGHIK"}],
                "num_diffusion_samples": 1,
            },
        },
    )
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "queued"

    print("smoke_backend: ok")


if __name__ == "__main__":
    main()
