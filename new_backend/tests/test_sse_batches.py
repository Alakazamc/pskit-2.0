import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageDeltaData, MessageDeltaEvent, MessageRequest
from app.main import create_app


def _sse_events(body: str) -> list[dict]:
    return [json.loads(line.removeprefix("data: "))
            for line in body.splitlines() if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_sse_replays_large_history_in_bounded_batches_without_losing_cursors(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    store = app.state.conversations
    token, user = app.state.demo_store.issue_token("alice@example.org")
    user_id = user.id
    project_id = store.project_for(user_id).id
    session_id = store.sessions_for(user_id, project_id)[0].id
    run_id = store.send_message(user_id, session_id, MessageRequest(content="test")).run_id
    for index in range(300):
        store.append_event(user_id, run_id,
                           MessageDeltaEvent(run_id=run_id,
                                             data=MessageDeltaData(delta=str(index))))
    store.set_run_status(run_id, "completed")
    fetch_sizes: list[int | None] = []
    original = store.events_for

    def bounded_events(owner, run, after, *, limit=None):
        fetch_sizes.append(limit)
        return original(owner, run, after, limit=limit)

    store.events_for = bounded_events
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        all_events = await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)
        tail = await client.get(f"/api/v1/runs/{run_id}/events?after=250", headers=headers)
    assert all_events.status_code == tail.status_code == 200
    assert [event["id"] for event in _sse_events(all_events.text)] == [str(i) for i in range(1, 301)]
    assert [event["id"] for event in _sse_events(tail.text)] == [str(i) for i in range(251, 301)]
    assert fetch_sizes and all(size is not None and size <= 128 for size in fetch_sizes)


@pytest.mark.asyncio
async def test_sse_does_not_fetch_next_batch_while_client_send_is_blocked(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    token, user = app.state.demo_store.issue_token("alice@example.org")
    store = app.state.conversations
    session_id = store.sessions_for(user.id, store.project_for(user.id).id)[0].id
    run_id = store.send_message(user.id, session_id, MessageRequest(content="test")).run_id
    for index in range(300):
        store.append_event(user.id, run_id,
                           MessageDeltaEvent(run_id=run_id,
                                             data=MessageDeltaData(delta=str(index))))
    store.set_run_status(run_id, "completed")
    fetch_sizes: list[int | None] = []
    original = store.events_for

    def bounded_events(owner, run, after, *, limit=None):
        fetch_sizes.append(limit)
        return original(owner, run, after, limit=limit)

    store.events_for = bounded_events
    first_send = asyncio.Event()
    resume_send = asyncio.Event()
    chunks: list[bytes] = []

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not first_send.is_set():
                first_send.set()
                await resume_send.wait()

    async def receive():
        await asyncio.Event().wait()

    path = f"/api/v1/runs/{run_id}/events"
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"follow=false", "root_path": "",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(first_send.wait(), timeout=3)
        await asyncio.sleep(0.02)
        assert fetch_sizes == [1, 128]
    finally:
        resume_send.set()
        await asyncio.wait_for(task, timeout=3)
    assert len(_sse_events(b"".join(chunks).decode())) == 300
