import asyncio

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest, ToolFinishedData, ToolFinishedEvent
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app


class ToolEventPi:
    async def prompt(self, session_id, message, on_event, **kwargs):
        await on_event({"type": "message_start", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "private draft"}],
        }})
        await on_event({
            "type": "tool_execution_start", "toolCallId": "call-1",
            "toolName": "search_pdb", "args": {"query": "private input"},
        })
        await on_event({
            "type": "tool_execution_update", "toolCallId": "call-1",
            "toolName": "search_pdb", "partialResult": {
                "content": [{"type": "text", "text": "private partial output"}],
            },
        })
        await on_event({
            "type": "tool_execution_end", "toolCallId": "call-1",
            "toolName": "search_pdb", "result": {
                "content": [{"type": "text", "text": "private tool result"}],
                "details": {},
            }, "isError": False,
        })
        await on_event({"type": "message_end", "message": {
            "role": "assistant", "usage": {"totalTokens": 7},
            "content": [{"type": "text", "text": "private final"}],
        }})
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Found a structure"}


@pytest.mark.asyncio
async def test_pi_tool_lifecycle_is_typed_and_projected_to_message_without_raw_payload(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=ToolEventPi())
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start lifespan before ASGI requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            identity = (await client.post("/api/v1/auth/demo", json={
                "email": "alice@example.org",
            })).json()
            headers = {"Authorization": f"Bearer {identity['access_token']}"}
            session_id = f"session-{identity['user']['id']}"
            sent = await client.post(f"/api/v1/c/{session_id}/messages", headers=headers,
                                     json={"content": "Search PDB"})
            run_id = sent.json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            events = (await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                       headers=headers)).text
            messages = (await client.get(f"/api/v1/c/{session_id}/messages",
                                         headers=headers)).json()

    assert sent.status_code == 200 and status == "completed"
    assert 'event: tool.started' in events
    assert 'event: message.start' in events
    assert 'event: message.end' in events
    assert 'event: tool.updated' in events
    assert 'event: tool.finished' in events
    assert '"tool_call_id":"call-1"' in events
    assert "private input" not in events
    assert "private partial output" not in events
    assert "private tool result" not in events
    assert "private draft" not in events
    assert "private final" not in events
    assert messages[-1]["parts"] == [
        {"type": "text", "text": "Found a structure"},
        {"type": "tool_call", "tool_call_id": "call-1", "tool": "search_pdb",
         "status": "completed", "summary": "Tool completed"},
    ]


def test_completed_background_job_updates_projected_tool_part(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    accepted = store.accept_message("alice", "session-alice", MessageRequest(content="Run AF3"),
                                    None, 2, None, "hash")
    assert accepted is not None
    run_id = accepted[0].run_id
    store.append_event("alice", run_id, ToolFinishedEvent(
        run_id=run_id, data=ToolFinishedData(
            tool_call_id="call-1", tool="submit_af3", status="pending",
            summary="Background task submitted",
        ),
    ))
    job = store.create_af3_job("alice", 20, run_id, "call-1")
    store.settle_af3_job(job.id, "completed", 18, [{
        "id": "artifact-1", "name": "prediction.cif", "kind": "structure",
    }])
    store.add_assistant_reply("alice", run_id, "Finished analysis")

    messages = store.messages_for("alice", "session-alice")
    assert messages is not None
    assert messages[-1].parts[1].status == "completed"
    assert messages[-1].parts[1].summary == "Background task completed"
    assert messages[-1].parts[2].type == "artifact"
    assert messages[-1].parts[2].id == "artifact-1"
