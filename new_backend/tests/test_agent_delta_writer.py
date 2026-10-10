import asyncio

import pytest

from app.contracts.conversation import (
    MessageDeltaData,
    MessageDeltaEvent,
    MessageEndEvent,
    ToolStartedData,
    ToolStartedEvent,
)
from app.services.agent_events import DeltaEventWriter


@pytest.mark.asyncio
async def test_empty_provider_delta_does_not_delay_first_visible_token():
    batches = []

    async def persist(events):
        batches.extend(events)

    writer = DeltaEventWriter("run", persist)
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="")))
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="visible")))
    assert "visible" in [event.data.delta for event in batches]
    await writer.close()


@pytest.mark.asyncio
async def test_first_delta_is_immediate_then_adjacent_text_is_coalesced_before_tool():
    batches = []

    async def persist(events):
        batches.append(events)

    writer = DeltaEventWriter("run", persist, flush_seconds=0.1, max_bytes=4096)
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="a")))
    assert len(batches) == 1
    for _ in range(255):
        await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="b")))
    await writer.write(
        ToolStartedEvent(
            run_id="run",
            data=ToolStartedData(
                tool_call_id="call",
                tool="read_file",
            ),
        )
    )
    await writer.close()
    assert [event.type for batch in batches for event in batch] == [
        "message.delta",
        "message.delta",
        "tool.started",
    ]
    assert batches[1][0].data.delta == "b" * 255


@pytest.mark.asyncio
async def test_timer_flushes_a_partial_burst_while_provider_is_waiting():
    batches = []

    async def persist(events):
        batches.append(events)

    writer = DeltaEventWriter("run", persist, flush_seconds=0.01, max_bytes=4096)
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="first")))
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="next")))
    await asyncio.sleep(0.03)
    assert len(batches) == 2
    await writer.close()


@pytest.mark.asyncio
async def test_multibyte_byte_limit_and_terminal_flush_preserve_text():
    batches = []

    async def persist(events):
        batches.append(events)

    writer = DeltaEventWriter("run", persist, max_bytes=6)
    for value in ["a", "中", "文", "b"]:
        await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta=value)))
    await writer.write(MessageEndEvent(run_id="run"))
    await writer.close()
    events = [event for batch in batches for event in batch]
    assert (
        "".join(event.data.delta for event in events if event.type == "message.delta") == "a中文b"
    )
    assert events[-1].type == "message.end"


@pytest.mark.asyncio
async def test_close_flushes_unfinished_delta_and_observes_timer_failure():
    async def persist(events):
        if events[0].data.delta == "next":
            raise RuntimeError("synthetic database failure")

    writer = DeltaEventWriter("run", persist, flush_seconds=0.01)
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="first")))
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="next")))
    await asyncio.sleep(0.03)
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        await writer.close()


@pytest.mark.asyncio
async def test_close_waits_for_timer_commit_without_replaying_text():
    committing = asyncio.Event()
    acknowledged = asyncio.Event()
    batches = []

    async def persist(events):
        if events[0].data.delta == "next":
            committing.set()
            await acknowledged.wait()
        batches.append(events)

    writer = DeltaEventWriter("run", persist, flush_seconds=0.01)
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="first")))
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta="next")))
    await committing.wait()
    closing = asyncio.create_task(writer.close())
    await asyncio.sleep(0)
    assert not closing.done()
    acknowledged.set()
    await closing
    assert [event.data.delta for batch in batches for event in batch] == ["first", "next"]


@pytest.mark.asyncio
async def test_one_oversized_delta_is_split_on_unicode_boundaries():
    batches = []

    async def persist(events):
        batches.append(events)

    writer = DeltaEventWriter("run", persist, max_bytes=6)
    text = "中文🙂abc" * 10
    await writer.write(MessageDeltaEvent(run_id="run", data=MessageDeltaData(delta=text)))
    await writer.close()
    deltas = [event.data.delta for batch in batches for event in batch]
    assert "".join(deltas) == text
    assert all(len(delta.encode("utf-8")) <= 6 for delta in deltas)
