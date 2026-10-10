"""Typed Pi projection and bounded text-delta persistence."""

import asyncio

from app.contracts.conversation import (
    MessageDeltaData,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    RunEvent,
    RunRetryingData,
    RunRetryingEvent,
    ToolFinishedData,
    ToolFinishedEvent,
    ToolStartedData,
    ToolStartedEvent,
    ToolUpdatedData,
    ToolUpdatedEvent,
)


def project_pi_event(run_id: str, event: dict) -> list[RunEvent]:
    """Translate a Pi update without database access or timing side effects."""
    events = []
    if event.get("type") in {"message_start", "message_end"}:
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            projected = (
                MessageStartEvent(run_id=run_id)
                if event["type"] == "message_start"
                else MessageEndEvent(run_id=run_id)
            )
            events.append(projected)
    elif event.get("type") == "message_update":
        update = event.get("assistantMessageEvent", {})
        if update.get("type") == "text_delta":
            events.append(
                MessageDeltaEvent(run_id=run_id, data=MessageDeltaData(delta=update["delta"])),
            )
    elif event.get("type") == "auto_retry_start":
        attempt, maximum, delay = (
            event.get("attempt"),
            event.get("maxAttempts"),
            event.get("delayMs"),
        )
        if all(type(value) is int for value in (attempt, maximum, delay)):
            events.append(
                RunRetryingEvent(
                    run_id=run_id,
                    data=RunRetryingData(
                        attempt=attempt,
                        max_attempts=maximum,
                        delay_ms=delay,
                    ),
                ),
            )
    elif event.get("type") in {
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
    }:
        tool_call_id, tool = event.get("toolCallId"), event.get("toolName")
        if not isinstance(tool_call_id, str) or not isinstance(tool, str):
            return []
        common = ToolStartedData(tool_call_id=tool_call_id, tool=tool)
        if event["type"] == "tool_execution_start":
            projected = ToolStartedEvent(run_id=run_id, data=common)
        elif event["type"] == "tool_execution_update":
            partial = event.get("partialResult") or {}
            blocks = partial.get("content") if isinstance(partial, dict) else None
            text = "\n".join(
                block.get("text", "")
                for block in blocks or []
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            summary = text.splitlines()[-1][-500:] if text else None
            projected = ToolUpdatedEvent(
                run_id=run_id,
                data=ToolUpdatedData(
                    tool_call_id=tool_call_id,
                    tool=tool,
                    summary=summary,
                ),
            )
        else:
            details = (event.get("result") or {}).get("details") or {}
            state = details.get("status") if isinstance(details, dict) else None
            status = (
                "failed"
                if event.get("isError") or state == "failed"
                else state
                if state in {"pending", "approval_required"}
                else "completed"
            )
            summary = {
                "failed": "Tool failed",
                "pending": "Background task submitted",
                "approval_required": "Waiting for approval",
                "completed": "Tool completed",
            }[status]
            projected = ToolFinishedEvent(
                run_id=run_id,
                data=ToolFinishedData(
                    tool_call_id=tool_call_id,
                    tool=tool,
                    status=status,
                    summary=summary,
                ),
            )
        events.append(projected)

    return events


class DeltaEventWriter:
    """Persist the first token immediately and coalesce only adjacent text.

    Later text has a 100 ms / 4 KiB flush boundary. Tool, retry, message-end,
    artifact and terminal events flush text first. A process crash can lose
    only this uncommitted text window; critical events are never buffered.
    """

    def __init__(self, run_id, persist, *, flush_seconds=0.1, max_bytes=4096):
        if flush_seconds <= 0 or max_bytes < 4:
            raise ValueError("Positive flush time and at least four UTF-8 bytes are required")
        self.run_id = run_id
        self.persist = persist
        self.flush_seconds = flush_seconds
        self.max_bytes = max_bytes
        self._lock = asyncio.Lock()
        self._chunks: list[str] = []
        self._bytes = 0
        self._first = True
        self._timer: asyncio.Task | None = None
        self._error: BaseException | None = None
        self._closed = False

    def _raise_error(self):
        if self._error is not None:
            raise self._error

    async def _flush_locked(self):
        self._raise_error()
        if self._chunks:
            event = MessageDeltaEvent(
                run_id=self.run_id, data=MessageDeltaData(delta="".join(self._chunks))
            )
            task = asyncio.create_task(self.persist([event]))
            cancelled = False
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                cancelled = True
            self._chunks.clear()
            self._bytes = 0
            if cancelled:
                raise asyncio.CancelledError

    async def _timed_flush(self):
        try:
            await asyncio.sleep(self.flush_seconds)
            async with self._lock:
                if not self._closed:
                    await self._flush_locked()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - observed by write/close, never discarded.
            self._error = error
        finally:
            self._timer = None

    def _split_text(self, text):
        encoded = text.encode("utf-8")
        if len(encoded) <= self.max_bytes:
            yield text
            return
        start = 0
        while start < len(encoded):
            end = min(len(encoded), start + self.max_bytes)
            # Do not cut a Unicode code point across persisted public events.
            while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
                end -= 1
            yield encoded[start:end].decode("utf-8")
            start = end

    async def write(self, event: RunEvent):
        async with self._lock:
            self._raise_error()
            if self._closed:
                raise RuntimeError("Delta writer is closed")
            if isinstance(event, MessageDeltaEvent):
                for delta in self._split_text(event.data.delta):
                    event = event.model_copy(update={"data": MessageDeltaData(delta=delta)})
                    if self._first:
                        await self.persist([event])
                        # Empty provider frames carry no visible token; the
                        # next nonempty delta still needs immediate persistence.
                        self._first = not bool(delta)
                        continue
                    encoded = len(event.data.delta.encode("utf-8"))
                    if self._bytes and self._bytes + encoded > self.max_bytes:
                        await self._flush_locked()
                    self._chunks.append(event.data.delta)
                    self._bytes += encoded
                    if self._bytes >= self.max_bytes:
                        await self._flush_locked()
                    elif self._timer is None:
                        self._timer = asyncio.create_task(self._timed_flush())
            else:
                await self._flush_locked()
                await self.persist([event])
                if event.type in {"message.start", "message.end", "run.retrying"}:
                    self._first = True

    async def close(self):
        # Acquire before cancelling: a timer already committing SQL must finish
        # and clear its acknowledged buffer, otherwise close could replay text.
        async with self._lock:
            self._closed = True
            await self._flush_locked()
        timer = self._timer
        if timer is not None:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    async def flush(self):
        async with self._lock:
            await self._flush_locked()


class AgentProgressWriter:
    """One Pi turn's usage, lease fence, projection, and ordered text buffer."""

    def __init__(self, service, user_id, run_id, *, reservation_period=None):
        self.service = service
        self.user_id = user_id
        self.run_id = run_id
        self.reservation_period = reservation_period

        async def persist(events):
            return await service._db_call(
                service.store.append_events,
                user_id,
                run_id,
                events,
                lease_owner=service._instance_id,
            )

        self.writer = DeltaEventWriter(run_id, persist)

    async def on_event(self, event):
        service = self.service
        if not await service._db_call(service.store.owns_lease, self.run_id, service._instance_id):
            return
        if event.get("type") != "message_update":
            await self.writer.flush()
        tokens = service._reported_tokens(event)
        if tokens:
            message = event["message"]
            await service._db_call(
                service.store.record_model_attempt,
                self.user_id,
                self.run_id,
                tokens,
                "error" if message.get("stopReason") in {"error", "aborted"} else "completed",
                **({"period_override": self.reservation_period} if self.reservation_period else {}),
            )
        elif (
            event.get("type") == "message_end"
            and (event.get("message") or {}).get("role") == "assistant"
        ):
            await service._db_call(
                service.store.record_unreported_model_call, self.user_id, self.run_id
            )
        for projected in project_pi_event(self.run_id, event):
            await self.writer.write(projected)

    async def close(self):
        await self.writer.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, error_type, error, traceback):
        await self.close()
