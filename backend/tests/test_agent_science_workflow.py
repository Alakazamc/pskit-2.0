"""Real Agent/queue/worker/download contract with only model boundaries replaced."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent import orchestrator
from app.main import app
from app.tasks import worker


def test_agent_queues_science_task_and_owner_can_download_worker_result(monkeypatch):
    monkeypatch.setattr(orchestrator, "retrieve", lambda *args, **kwargs: ("test", []))
    monkeypatch.setattr(orchestrator.OpenAICompatibleClient, "chat", lambda *args, **kwargs: {
        "role": "assistant", "content": "", "tool_calls": [{
            "id": "test-interaction", "type": "function", "function": {
                "name": "predict_interaction",
                "arguments": json.dumps({"protein_sequence": "ACDE", "nucleic_sequence": "ACGU"}),
            },
        }],
    })

    def scientific_model(_task, output_dir):
        # This fixture is not evidence of a real model inference result.
        (output_dir / "interaction.json").write_text('{"fixture_score":0.9}', encoding="utf-8")
        return {"stdout": "", "fixture": True}

    monkeypatch.setattr(worker, "run_interaction_task", scientific_model)
    owner = TestClient(app)
    assert owner.post("/api/auth/register", json={
        "username": "science-owner", "password": "password123",
    }).status_code == 200
    session_id = owner.post("/api/agent/sessions", json={"title": "science"}).json()["id"]
    payload = {"content": "请调用 predict_interaction 预测 ACDE 和 ACGU 的相互作用", "turn_id": str(uuid4())}
    response = owner.post(f"/api/agent/sessions/{session_id}/message", json=payload)
    assert response.status_code == 200
    tasks = owner.get("/api/tasks").json()
    assert len(tasks) == 1, response.text
    assert tasks[0]["status"] == "queued"
    assert tasks[0]["session_id"] == session_id
    assert worker.run_once(initialize=False)
    completed = owner.get(f"/api/tasks/{tasks[0]['id']}").json()
    assert completed["status"] == "succeeded", completed
    result = next(item for item in completed["artifacts"] if item["filename"] == "interaction.json")
    assert owner.get(result["download_url"]).json() == {"fixture_score": 0.9}

    other = TestClient(app)
    assert other.post("/api/auth/register", json={
        "username": "science-other", "password": "password123",
    }).status_code == 200
    assert other.get(result["download_url"]).status_code == 404
    replay = owner.post(f"/api/agent/sessions/{session_id}/message", json=payload)
    assert replay.status_code == 200
    assert len(owner.get("/api/tasks").json()) == 1
