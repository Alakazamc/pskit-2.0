"""Session concurrency and confirmed cancellation at public coordinator/manager ports."""

import asyncio

import httpx
import pytest
from test_sandbox_lifecycle import AUTH, DockerDouble, settings

from app.contracts.sandbox import SandboxPromptRequest
from app.domain.sandboxes import MemorySandboxActivityStore
from app.sandbox_manager import create_manager_app
from app.services.sandbox_sessions import SandboxSessionCoordinator


def request(session, attempt):
    return SandboxPromptRequest(
        user_id="alice", session_id=session, attempt_id=attempt, message="hi", model="m"
    )


@pytest.mark.asyncio
async def test_concurrent_same_session_is_serialized(tmp_path):
    entered = []
    release = asyncio.Event()

    class Pi:
        async def prompt(self, session, message, on_event, **kwargs):
            entered.append((session, kwargs))
            await release.wait()
            return {"session_file": str(tmp_path / session / ".pi/turn"), "text": "done"}

    coordinator = SandboxSessionCoordinator(tmp_path, lambda _: Pi())
    tasks = [
        asyncio.create_task(coordinator.prompt(request(s, a), lambda _: None))
        for s, a in [("one", "a"), ("one", "b"), ("two", "c")]
    ]
    for _ in range(10):
        await asyncio.sleep(0)
    assert [item[0] for item in entered] == ["one", "two"]
    release.set()
    await asyncio.gather(*tasks)
    assert [item[0] for item in entered] == ["one", "two", "one"]
    assert entered[0][1]["working_directory"] != entered[1][1]["working_directory"]


@pytest.mark.asyncio
async def test_cancel_waits_for_pi_exit(tmp_path):
    running = asyncio.Event()
    exited = asyncio.Event()

    class Pi:
        async def prompt(self, *args, **kwargs):
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                await exited.wait()

    coordinator = SandboxSessionCoordinator(tmp_path, lambda _: Pi())
    task = asyncio.create_task(coordinator.prompt(request("one", "a"), lambda _: None))
    await running.wait()
    assert (await coordinator.cancel("a")).state == "cancelling"
    assert coordinator.status("a").exited is False
    exited.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert coordinator.status("a").state == "cancelled"
    assert coordinator.status("a").exited is True


@pytest.mark.asyncio
async def test_manager_restart_checks_bridge_before_reaping():
    clock = [0.0]
    store = MemorySandboxActivityStore(clock=lambda: clock[0])
    docker = DockerDouble()
    reports = [True]

    def bridge(_):
        if reports[0] is None:
            raise httpx.ConnectError("lost")
        return httpx.Response(
            200,
            json={
                "boot_id": "boot",
                "attempts": [
                    {"attempt_id": "a", "session_id": "one", "state": "running", "exited": False}
                ]
                if reports[0]
                else [],
            },
        )

    def app():
        return create_manager_app(
            settings(),
            activity_store=store,
            clock=lambda: clock[0],
            docker_transport=httpx.MockTransport(docker),
            bridge_transport=httpx.MockTransport(bridge),
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://manager", headers=AUTH
    ) as client:
        await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
    restarted = app()
    async with restarted.router.lifespan_context(restarted):
        clock[0] += 1801
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://manager", headers=AUTH
        ) as client:
            assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 0}
            reports[0] = None
            assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 0}
            summary = (await client.get("/v1/sandboxes")).json()[0]
            assert summary["runtime_state"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [None, {"state": "running", "exited": True}])
async def test_invalid_bridge_activity_remains_unknown(attempt):
    clock = [0.0]
    store = MemorySandboxActivityStore(clock=lambda: clock[0])
    app = create_manager_app(
        settings(),
        activity_store=store,
        clock=lambda: clock[0],
        docker_transport=httpx.MockTransport(DockerDouble()),
        bridge_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"boot_id": "boot", "attempts": [attempt]})
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        clock[0] = 1801
        assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 0}
        assert (await client.get("/v1/sandboxes")).json()[0]["runtime_state"] == "unknown"
