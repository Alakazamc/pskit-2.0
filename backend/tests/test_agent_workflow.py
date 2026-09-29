from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent import orchestrator
from app.db.models import AgentMessage, AgentTurn, Task
from app.db.session import SessionLocal
from app.main import app


def test_agent_sse_persists_once_and_replay_is_idempotent(monkeypatch):
    calls = {"planner": 0, "synthesizer": 0, "rag": 0}

    def fake_retrieve(_query: str, top_k: int = 5):
        assert top_k == 5
        calls["rag"] += 1
        return "test", []

    def fake_chat(self, messages, **_kwargs):
        assert messages
        calls["planner"] += 1
        return {"role": "assistant", "content": "", "tool_calls": []}

    def fake_stream_chat_sync(self, messages):
        assert messages
        calls["synthesizer"] += 1
        yield "这是"
        yield "离线测试回答。"

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", fake_chat)
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "stream_chat_sync",
        fake_stream_chat_sync,
    )

    client = TestClient(app)
    username = "agent_workflow_" + uuid.uuid4().hex[:10]
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert registered.status_code == 200
    session = client.post("/api/agent/sessions", json={"title": "workflow"})
    assert session.status_code == 200
    session_id = session.json()["id"]
    client_turn_id = str(uuid.uuid4())
    payload = {"content": "请给我一句测试回答", "turn_id": client_turn_id}

    first = client.post(f"/api/agent/sessions/{session_id}/message", json=payload)
    assert first.status_code == 200
    assert "离线测试回答" in first.text
    assert '"type": "message_done"' in first.text
    assert '"status": "succeeded"' in first.text

    history = client.get(f"/api/agent/sessions/{session_id}")
    assert history.status_code == 200
    assert [(item["role"], item["content"]) for item in history.json()] == [
        ("user", payload["content"]),
        ("assistant", "这是离线测试回答。"),
    ]

    replay = client.post(f"/api/agent/sessions/{session_id}/message", json=payload)
    assert replay.status_code == 200
    assert '"recovered": true' in replay.text
    assert calls == {"planner": 1, "synthesizer": 1, "rag": 1}

    db = SessionLocal()
    try:
        turns = db.scalars(
            select(AgentTurn).where(AgentTurn.client_turn_id == uuid.UUID(client_turn_id))
        ).all()
        assert len(turns) == 1
        assert turns[0].status == "succeeded"
        assert turns[0].assistant_message_id is not None
        assert db.scalar(
            select(func.count())
            .select_from(AgentMessage)
            .where(AgentMessage.session_id == uuid.UUID(session_id))
        ) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    finally:
        db.close()
