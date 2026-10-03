"""The public Pi runner contract works through private sandbox HTTP services."""

import httpx
import pytest
from fastapi import FastAPI

from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.sandbox_bridge import create_bridge_app


class FakePi:
    async def prompt(self, session_id, message, on_event, **_kwargs):
        await on_event({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "delta": "沙箱完成",
        }})
        return {"session_file": f"/workspace/sessions/{session_id}/turn.jsonl",
                "text": "沙箱完成"}


@pytest.mark.asyncio
async def test_agent_runner_streams_pi_from_the_users_sandbox():
    manager = FastAPI()

    @manager.post("/v1/sandboxes/ensure")
    async def ensure():
        return {"base_url": "http://pskit-sbx-test-123:8091", "token": "bridge-secret"}

    selected_models = []

    def make_pi(model):
        selected_models.append(model)
        return FakePi()

    bridge = create_bridge_app("user-1", "bridge-secret", make_pi)
    runner = SandboxPiRunner(
        manager_url="http://sandbox-manager:8090", manager_token="manager-secret",
        model="claude-opus-4-8", manager_transport=httpx.ASGITransport(app=manager),
        bridge_transport=httpx.ASGITransport(app=bridge),
    )
    events = []

    result = await runner.prompt("session-1", "读文件", events.append,
                                 environment={"PSKIT_USER_ID": "user-1", "PSKIT_RUN_ID": "run-1"})

    assert result == {"session_file": "/workspace/sessions/session-1/turn.jsonl",
                      "text": "沙箱完成"}
    assert events[0]["assistantMessageEvent"]["delta"] == "沙箱完成"
    assert selected_models == ["claude-opus-4-8"]

    selected_models.clear()
    await runner.prompt("session-2", "读图片", lambda _: None,
                        environment={"PSKIT_USER_ID": "user-1",
                                     "PSKIT_MODEL_ID": "vision-model"})
    assert selected_models == ["vision-model"]


@pytest.mark.asyncio
async def test_agent_runner_requires_backend_supplied_owner():
    runner = SandboxPiRunner(manager_url="http://sandbox-manager:8090",
                             manager_token="manager-secret", model="model")
    with pytest.raises(ValueError, match="owner"):
        await runner.prompt("session-1", "hi", lambda _: None)


@pytest.mark.asyncio
async def test_existing_backend_pi_transcript_is_imported_into_users_sandbox(tmp_path, monkeypatch):
    legacy = tmp_path / "old-turn.jsonl"
    legacy.write_text('{"type":"session"}\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path / "sandbox-sessions"))
    observed = []

    class ImportingPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            from pathlib import Path

            imported = Path(kwargs["session_file"])
            observed.append(imported.read_text())
            return {"session_file": str(imported.parent / "new-turn.jsonl"), "text": "继续"}

    manager = FastAPI()

    @manager.post("/v1/sandboxes/ensure")
    async def ensure():
        return {"base_url": "http://pskit-sbx-test-123:8091", "token": "bridge-secret"}

    bridge = create_bridge_app("user-1", "bridge-secret", lambda _: ImportingPi())
    runner = SandboxPiRunner(
        manager_url="http://sandbox-manager:8090", manager_token="manager-secret",
        model="model", manager_transport=httpx.ASGITransport(app=manager),
        bridge_transport=httpx.ASGITransport(app=bridge),
    )

    result = await runner.prompt(
        "session-1", "继续", lambda _: None, session_file=str(legacy),
        environment={"PSKIT_USER_ID": "user-1"},
    )

    assert observed == ['{"type":"session"}\n']
    assert result["session_file"].endswith("/sandbox-sessions/session-1/new-turn.jsonl")
    assert legacy.read_text() == '{"type":"session"}\n'


@pytest.mark.asyncio
async def test_new_sandbox_waits_until_private_bridge_is_ready():
    manager = FastAPI()

    @manager.post("/v1/sandboxes/ensure")
    async def ensure():
        return {"base_url": "http://pskit-sbx-test-123:8091", "token": "bridge-secret"}

    class StartingBridge(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(
                app=create_bridge_app("user-1", "bridge-secret", lambda _: FakePi()),
            )
            self.probes = 0

        async def handle_async_request(self, request):
            if request.url.path == "/health" and self.probes < 2:
                self.probes += 1
                raise httpx.ConnectError("starting", request=request)
            return await self.inner.handle_async_request(request)

    bridge = StartingBridge()
    runner = SandboxPiRunner(
        manager_url="http://sandbox-manager:8090", manager_token="manager-secret",
        model="model", manager_transport=httpx.ASGITransport(app=manager),
        bridge_transport=bridge,
    )

    result = await runner.prompt("session-1", "继续", lambda _: None,
                                 environment={"PSKIT_USER_ID": "user-1"})

    assert result["text"] == "沙箱完成"
    assert bridge.probes == 2


@pytest.mark.asyncio
async def test_stream_accepts_many_small_events_in_one_transport_chunk():
    manager = FastAPI()

    @manager.post("/v1/sandboxes/ensure")
    async def ensure():
        return {"base_url": "http://pskit-sbx-test-123:8091", "token": "bridge-secret"}

    class ManyEventsPi:
        async def prompt(self, session_id, message, on_event, **_kwargs):
            for _ in range(1100):
                await on_event({"type": "message_update", "value": "a" * 4096})
            return {"session_file": f"/workspace/sessions/{session_id}/turn.jsonl",
                    "text": "done"}

    bridge = create_bridge_app("user-1", "bridge-secret", lambda _: ManyEventsPi())
    runner = SandboxPiRunner(
        manager_url="http://sandbox-manager:8090", manager_token="manager-secret",
        model="model", manager_transport=httpx.ASGITransport(app=manager),
        bridge_transport=httpx.ASGITransport(app=bridge),
    )
    events = []

    result = await runner.prompt("session-1", "hi", events.append,
                                 environment={"PSKIT_USER_ID": "user-1"})

    assert result["text"] == "done"
    assert len(events) == 1100
