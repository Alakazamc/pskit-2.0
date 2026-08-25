import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.execution import recent_chat_messages
from app.artifacts.service import register_local_artifact
from app.db.models import AgentMessage, AgentSession, User, now_utc
from app.db.session import SessionLocal, init_db
from app.main import app
from app.tools.external import ToolExecutionError, read_result_file


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

    assert (
        owner.post(
            "/api/auth/register", json={"username": owner_name, "password": "password123"}
        ).status_code
        == 200
    )
    assert (
        other.post(
            "/api/auth/register", json={"username": other_name, "password": "password123"}
        ).status_code
        == 200
    )

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


def test_invalid_task_type_and_oversized_sequence_are_rejected():
    client = TestClient(app)
    username = "validation_" + uuid.uuid4().hex[:10]
    assert (
        client.post(
            "/api/auth/register", json={"username": username, "password": "password123"}
        ).status_code
        == 200
    )

    unsupported = client.post("/api/tasks", json={"task_type": "shell", "input": {}})
    assert unsupported.status_code == 422

    oversized = client.post(
        "/api/tasks",
        json={
            "task_type": "predict_interaction",
            "input": {"protein_sequence": "A" * 20001, "nucleic_sequence": "ACGU"},
        },
    )
    assert oversized.status_code == 422

    missing_mcp_arguments = client.post(
        "/api/tasks",
        json={"task_type": "generate_pepccd_candidates", "input": {}},
    )
    assert missing_mcp_arguments.status_code == 422


def test_artifact_reader_enforces_user_ownership():
    db = SessionLocal()
    try:
        owner = User(username="owner", password_hash="unused", role="user")
        other = User(username="other", password_hash="unused", role="user")
        db.add_all([owner, other])
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        session = AgentSession(user_id=owner.id, title="private")
        db.add(session)
        db.commit()
        db.refresh(session)

        path = Path(__import__("os").environ["ARTIFACT_DIR"]) / str(owner.id) / "secret.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private result", encoding="utf-8")
        artifact = register_local_artifact(db, owner, session.id, None, path, "log", "text/plain")

        with pytest.raises(ToolExecutionError, match="Artifact not found"):
            read_result_file(db, other, str(artifact.id))
        assert read_result_file(db, owner, str(artifact.id))["content"] == "private result"
    finally:
        db.close()


def test_recent_chat_messages_does_not_replay_abandoned_user_turns():
    db = SessionLocal()
    try:
        user = User(username="chat-history", password_hash="unused", role="user")
        db.add(user)
        db.commit()
        db.refresh(user)
        session = AgentSession(user_id=user.id, title="history")
        db.add(session)
        db.commit()
        db.refresh(session)

        started = now_utc()
        db.add_all(
            [
                AgentMessage(
                    session_id=session.id,
                    user_id=user.id,
                    role="user",
                    content="download an old structure",
                    created_at=started,
                ),
                AgentMessage(
                    session_id=session.id,
                    user_id=user.id,
                    role="user",
                    content="retry the old structure",
                    created_at=started + timedelta(seconds=1),
                ),
                AgentMessage(
                    session_id=session.id,
                    user_id=user.id,
                    role="user",
                    content="hello",
                    created_at=started + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()

        assert recent_chat_messages(db, session.id, user.id) == [
            {"role": "user", "content": "hello"}
        ]

        db.add(
            AgentMessage(
                session_id=session.id,
                user_id=user.id,
                role="assistant",
                content="Hi!",
                created_at=started + timedelta(seconds=3),
            )
        )
        db.add(
            AgentMessage(
                session_id=session.id,
                user_id=user.id,
                role="user",
                content="new question",
                created_at=started + timedelta(seconds=4),
            )
        )
        db.commit()

        assert recent_chat_messages(db, session.id, user.id) == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "new question"},
        ]
    finally:
        db.close()
