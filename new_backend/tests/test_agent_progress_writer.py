"""Text persistence keeps the atomic lease fence without a duplicate lease read."""

from types import SimpleNamespace

import pytest

from app.services.agent import AgentService
from app.services.agent_events import AgentProgressWriter


def progress_service():
    calls = []
    state = {"live": True}
    events = []

    def owns_lease(run_id, owner):
        calls.append(("owns_lease", run_id, owner))
        return state["live"]

    def append_events(user_id, run_id, batch, *, lease_owner):
        calls.append(("append_events", user_id, run_id, lease_owner))
        if not state["live"]:
            return False
        events.extend(batch)
        return True

    def usage(*args, **kwargs):
        calls.append(("usage", args, kwargs))

    service = object.__new__(AgentService)
    service._instance_id = "worker"
    service.store = SimpleNamespace(
        database=None, owns_lease=owns_lease, append_events=append_events,
        record_model_attempt=usage, record_unreported_model_call=usage,
    )
    return service, calls, state, events


def text_event(text):
    return {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}}


@pytest.mark.asyncio
async def test_first_text_delta_waits_for_fenced_commit_without_a_preflight_lease_query():
    service, calls, _state, events = progress_service()
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event(text_event("first"))
    assert [event.data.delta for event in events] == ["first"]
    assert calls == [("append_events", "alice", "run", "worker")]
    await writer.close()


@pytest.mark.asyncio
async def test_buffered_text_is_fenced_again_when_the_lease_is_lost():
    service, calls, state, events = progress_service()
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event(text_event("first"))
    await writer.on_event(text_event("buffered"))
    state["live"] = False
    await writer.close()
    assert [event.data.delta for event in events] == ["first"]
    assert calls == [("append_events", "alice", "run", "worker")] * 2


@pytest.mark.asyncio
async def test_first_text_frame_from_a_stale_lease_cannot_publish():
    service, calls, state, events = progress_service()
    state["live"] = False
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event(text_event("late"))
    await writer.close()
    assert events == []
    assert calls == [("append_events", "alice", "run", "worker")]


@pytest.mark.asyncio
async def test_reported_usage_retains_its_preflight_lease_check():
    service, calls, _state, events = progress_service()
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event({"type": "message_end", "message": {"role": "assistant", "usage": {"totalTokens": 7}}})
    await writer.close()
    assert [call[0] for call in calls] == ["owns_lease", "usage", "append_events"]
    assert [event.type for event in events] == ["message.end"]


@pytest.mark.asyncio
async def test_stale_usage_and_tool_frames_keep_their_existing_fence():
    service, calls, state, events = progress_service()
    state["live"] = False
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event({"type": "message_end", "message": {"role": "assistant", "usage": {"totalTokens": 7}}})
    await writer.on_event({"type": "tool_execution_start", "toolCallId": "call", "toolName": "read"})
    await writer.close()
    assert [call[0] for call in calls] == ["owns_lease", "owns_lease"]
    assert events == []


@pytest.mark.asyncio
async def test_tool_event_flushes_adjacent_text_before_its_own_fenced_commit():
    service, _calls, _state, events = progress_service()
    writer = AgentProgressWriter(service, "alice", "run")
    await writer.on_event(text_event("first"))
    await writer.on_event(text_event("buffered"))
    await writer.on_event({"type": "tool_execution_start", "toolCallId": "call", "toolName": "read"})
    await writer.close()
    assert [event.type for event in events] == ["message.delta", "message.delta", "tool.started"]
    assert [event.data.delta for event in events[:2]] == ["first", "buffered"]
