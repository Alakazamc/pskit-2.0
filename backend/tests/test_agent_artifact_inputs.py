import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import orchestrator
from app.artifacts.service import register_local_artifact, session_artifact_dir
from app.db.models import User
from app.db.session import SessionLocal
from app.main import app
from app.tasks.worker import resolve_owned_input_file
from app.tools.catalog import openai_tool_schemas


@pytest.mark.parametrize("tool_name", ["extract_empirical_features", "search_structure_homologs"])
def test_agent_accepts_artifact_id_without_pdb_path_and_preserves_ownership(
    monkeypatch, tool_name: str
):
    schema = next(
        item["function"]["parameters"]
        for item in openai_tool_schemas({tool_name})
        if item["function"]["name"] == tool_name
    )
    assert [branch["required"] for branch in schema["anyOf"]] == [
        ["artifact_id"],
        ["pdb_path"],
    ]
    assert "pdb_path" not in schema.get("required", [])

    client = TestClient(app)
    username = f"artifact_agent_{uuid4().hex[:10]}"
    assert client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123"},
    ).status_code == 200
    session = client.post("/api/agent/sessions", json={"title": "Artifact input"})
    assert session.status_code == 200
    session_id = UUID(session.json()["id"])

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == username))
        assert user is not None
        path = session_artifact_dir(user.id, session_id, "source") / "source.pdb"
        path.write_text("HEADER    TEST STRUCTURE\nEND\n", encoding="utf-8")
        artifact = register_local_artifact(
            db, user, session_id, None, path, "structure", "chemical/x-pdb"
        )
        artifact_id = str(artifact.id)
    finally:
        db.close()

    monkeypatch.setattr(orchestrator, "retrieve", lambda *args, **kwargs: ("test", []))

    def fake_chat(_self, _messages, **kwargs):
        exposed_schema = next(
            item["function"]["parameters"]
            for item in kwargs["tool_schemas"]
            if item["function"]["name"] == tool_name
        )
        assert exposed_schema["anyOf"] == schema["anyOf"]
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call-{tool_name}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps({"artifact_id": artifact_id}),
                    },
                }
            ],
        }

    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", fake_chat)
    response = client.post(
        f"/api/agent/sessions/{session_id}/message",
        json={"content": f"Please run {tool_name} on my structure", "turn_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == tool_name
    assert tasks[0]["status"] == "queued"
    task_detail = client.get(f"/api/tasks/{tasks[0]['id']}?include_details=true").json()
    assert task_detail["input"]["artifact_id"] == artifact_id
    assert "pdb_path" not in task_detail["input"]

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == username))
        assert user is not None
        assert resolve_owned_input_file(db, user, {"artifact_id": artifact_id}) == path.resolve()
        other = User(username=f"other_{uuid4().hex[:10]}", password_hash="unused")
        db.add(other)
        db.flush()
        with pytest.raises(FileNotFoundError):
            resolve_owned_input_file(db, other, {"artifact_id": artifact_id})
    finally:
        db.close()
