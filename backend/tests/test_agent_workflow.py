from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent import orchestrator
from app.db.models import AgentMessage, AgentTurn, Task, User
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
    assert all("runtime_dispatch" not in item["metadata"] for item in history.json())

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
        user_message = db.get(AgentMessage, turns[0].user_message_id)
        assert user_message is not None
        assert "runtime_dispatch" in user_message.metadata_json
        assert db.scalar(
            select(func.count())
            .select_from(AgentMessage)
            .where(AgentMessage.session_id == uuid.UUID(session_id))
        ) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 0
    finally:
        db.close()


def _session_with_active_af3(client: TestClient) -> str:
    username = "active_af3_" + uuid.uuid4().hex[:10]
    assert client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123"},
    ).status_code == 200
    session_id = client.post(
        "/api/agent/sessions", json={"title": "active AF3"}
    ).json()["id"]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        assert user is not None
        db.add(Task(
            user_id=user.id,
            session_id=uuid.UUID(session_id),
            task_type="run_alphafold3",
            status="queued",
            input_json={"entities": [{"type": "protein", "sequence": "ACDE"}]},
            output_json={},
        ))
        db.commit()
    return session_id


def test_active_af3_allows_independent_pdb_lookup(monkeypatch):
    monkeypatch.setattr(orchestrator, "retrieve", lambda *_args, **_kwargs: ("test", []))
    planned_schemas: list[set[str]] = []
    planner_calls = 0
    tool_calls: list[str] = []

    def fake_chat(_self, _messages, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        planned_schemas.append({
            item["function"]["name"] for item in kwargs["tool_schemas"]
        })
        if planner_calls == 1:
            return {"role": "assistant", "content": "", "tool_calls": [{
                "id": "pdb-lookup", "type": "function", "function": {
                    "name": "search_pdb", "arguments": json.dumps({"query": "RNA polymerase"}),
                },
            }]}
        return {"role": "assistant", "content": "", "tool_calls": []}

    def fake_tool(name, _args, _context):
        tool_calls.append(name)
        return {"query": "RNA polymerase", "count": 0, "results": []}

    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", fake_chat)
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "stream_chat_sync",
        lambda _self, _messages: iter(["PDB 查询已完成。"]),
    )
    monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)

    client = TestClient(app)
    session_id = _session_with_active_af3(client)
    response = client.post(
        f"/api/agent/sessions/{session_id}/message",
        json={"content": "请搜索 RNA polymerase 的 PDB 条目", "turn_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    assert "PDB 查询已完成" in response.text
    assert tool_calls == ["search_pdb"]
    assert planned_schemas
    assert all(
        {"search_pdb", "list_task_artifacts", "read_result_file"} <= names
        and "run_alphafold3" not in names
        for names in planned_schemas
    )
    assert len(client.get("/api/tasks").json()) == 1


@pytest.mark.parametrize(
    "report_tool",
    ["generate_session_report", "generate_harness_report", "generate_research_report"],
)
def test_active_af3_allows_interim_report(monkeypatch, report_tool):
    monkeypatch.setattr(orchestrator, "retrieve", lambda *_args, **_kwargs: ("test", []))
    offered = []
    calls = []

    def fake_chat(_self, _messages, **kwargs):
        offered.append({item["function"]["name"] for item in kwargs["tool_schemas"]})
        if len(offered) == 1:
            return {"role": "assistant", "content": "", "tool_calls": [{
                "id": "interim-report", "type": "function", "function": {
                    "name": report_tool, "arguments": "{}",
                },
            }]}
        return {"role": "assistant", "content": "", "tool_calls": []}

    def fake_tool(name, _args, _context):
        calls.append(name)
        return {"artifact_id": str(uuid.uuid4()), "filename": "interim.md", "kind": "report"}

    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", fake_chat)
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "stream_chat_sync",
        lambda _self, _messages: iter(["阶段报告已生成。"]),
    )
    monkeypatch.setattr(orchestrator, "execute_tool", fake_tool)

    client = TestClient(app)
    session_id = _session_with_active_af3(client)
    response = client.post(
        f"/api/agent/sessions/{session_id}/message",
        json={"content": "请生成已完成部分的阶段报告", "turn_id": str(uuid.uuid4())},
    )

    assert response.status_code == 200
    assert "阶段报告已生成" in response.text
    assert calls == [report_tool]
    assert offered and all(report_tool in names for names in offered)
    assert len(client.get("/api/tasks").json()) == 1


def test_active_af3_rejects_unoffered_duplicate_long_tool(monkeypatch):
    monkeypatch.setattr(orchestrator, "retrieve", lambda *_args, **_kwargs: ("test", []))
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "chat",
        lambda _self, _messages, **_kwargs: {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "duplicate-af3", "type": "function", "function": {
                    "name": "run_alphafold3",
                    "arguments": json.dumps({"entities": [{"type": "protein", "sequence": "ACDE"}]}),
                },
            }],
        },
    )
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "stream_chat_sync",
        lambda _self, _messages: iter(["已有任务，未重复提交。"]),
    )
    monkeypatch.setattr(
        orchestrator,
        "execute_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate tool ran")),
    )

    client = TestClient(app)
    session_id = _session_with_active_af3(client)
    response = client.post(
        f"/api/agent/sessions/{session_id}/message",
        json={"content": "再做一次相同的结构预测", "turn_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    assert '"error_type": "active_task_blocks_tool"' in response.text
    assert len(client.get("/api/tasks").json()) == 1


def test_planner_sees_safe_failed_task_facts_without_worker_message(monkeypatch):
    monkeypatch.setattr(orchestrator, "retrieve", lambda *_args, **_kwargs: ("test", []))
    planner_messages = []

    def fake_chat(_self, messages, **_kwargs):
        planner_messages.extend(messages)
        return {"role": "assistant", "content": "", "tool_calls": []}

    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", fake_chat)
    monkeypatch.setattr(
        orchestrator.OpenAICompatibleClient,
        "stream_chat_sync",
        lambda _self, _messages: iter(["该任务失败，没有推理结果。"]),
    )

    client = TestClient(app)
    session_id = _session_with_active_af3(client)
    with SessionLocal() as db:
        task = db.scalar(select(Task))
        assert task is not None
        task_id = task.id
        task.status = "failed"
        task.error_type = "TaskLeaseExpired"
        task.error_message = "private-server-path:/secret/model/weights"
        task.output_json = {"output_dir": "/secret/output"}
        db.commit()

    response = client.post(
        f"/api/agent/sessions/{session_id}/message",
        json={"content": "刚才的任务为什么失败？", "turn_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    context = "\n".join(str(item["content"]) for item in planner_messages)
    assert str(task_id) in context
    assert '"error_type":"TaskLeaseExpired"' in context
    assert "private-server-path" not in context
    assert "output_dir" not in context


def test_compact_tool_result_keeps_bounded_artifact_ids_without_paths():
    artifacts = [
        {
            "artifact_id": str(uuid.uuid4()),
            "filename": f"C:\\private\\results\\model_{index}.cif",
            "kind": "structure",
            "file_path": f"C:\\private\\results\\model_{index}.cif",
        }
        for index in range(20)
    ]
    result = {
        "task_id": str(uuid.uuid4()),
        "status": "failed",
        "error_type": "TaskWorkerError",
        "offset": 10,
        "limit": 20,
        "total_count": 40,
        "next_offset": 30,
        "artifacts": artifacts,
    }
    encoded = orchestrator.compact_tool_result(result, max_chars=900)
    compact = json.loads(encoded)
    kept = compact["summary"]["artifacts"]
    assert compact["truncated"] is True
    assert 0 < len(kept) <= 8
    assert len(encoded) <= 900
    assert kept[0] == {
        "artifact_id": artifacts[0]["artifact_id"],
        "filename": "model_0.cif",
        "kind": "structure",
    }
    assert compact["summary"]["artifact_count"] == 20
    assert compact["summary"]["artifacts_omitted"] == 20 - len(kept)
    assert compact["summary"]["error_type"] == "TaskWorkerError"
    assert compact["summary"]["total_count"] == 40
    assert compact["summary"]["next_offset"] == 10 + len(kept)
    assert "next_offset" in compact["instruction"]
    assert "private" not in encoded
    assert "file_path" not in encoded

    last_page = json.loads(orchestrator.compact_tool_result(
        {**result, "offset": 20, "next_offset": None}, max_chars=900
    ))
    assert last_page["summary"]["next_offset"] == (
        20 + len(last_page["summary"]["artifacts"])
    )
