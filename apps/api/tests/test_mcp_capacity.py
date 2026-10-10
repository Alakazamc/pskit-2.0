import asyncio
import sqlite3
import time

import httpx
import pytest

from app.config import Settings
from app.contracts.capabilities import McpInvokeResult, McpTool
from app.main import create_app


@pytest.mark.asyncio
async def test_mcp_requests_share_a_bounded_execution_slot():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowMcp:
        def tools(self):
            return [
                McpTool(
                    name="slow_lookup", description="Slow lookup", input_schema={"type": "object"}
                )
            ]

        async def discover(self):
            return self.tools()

        async def invoke(self, name, arguments):
            started.set()
            await release.wait()
            return McpInvokeResult(tool=name, result={"ok": True})

    app = create_app(
        Settings(mcp_executor="remote", mcp_max_concurrent_calls=1, mcp_queue_timeout_seconds=0.01),
        mcp_provider=SlowMcp(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        url = "/api/v1/mcp/tools/slow_lookup/invoke"
        first = asyncio.create_task(client.post(url, json={}, headers=headers))
        await asyncio.wait_for(started.wait(), timeout=1)
        busy = await client.post(url, json={}, headers=headers)
        assert busy.status_code == 429
        assert busy.json()["detail"]["code"] == "MCP_CAPACITY_EXCEEDED"
        release.set()
        completed = await asyncio.wait_for(first, timeout=1)
        assert completed.status_code == 200
        later = await client.post(url, json={}, headers=headers)
        assert later.status_code == 200


@pytest.mark.asyncio
async def test_two_python_instances_share_mcp_capacity_through_the_same_database(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    second_calls = 0

    class SlowMcp:
        def tools(self):
            return [
                McpTool(
                    name="slow_lookup", description="Slow lookup", input_schema={"type": "object"}
                )
            ]

        async def discover(self):
            return self.tools()

        async def invoke(self, name, arguments):
            started.set()
            await release.wait()
            return McpInvokeResult(tool=name, result={"ok": True})

    class FastMcp(SlowMcp):
        async def invoke(self, name, arguments):
            nonlocal second_calls
            second_calls += 1
            return McpInvokeResult(tool=name, result={"ok": True})

    settings = Settings(
        agent_runtime="pi",
        agent_db_path=str(tmp_path / "shared.sqlite3"),
        mcp_executor="remote",
        mcp_max_concurrent_calls=1,
        mcp_queue_timeout_seconds=0.05,
    )
    first_app = create_app(settings, pi_runner=object(), mcp_provider=SlowMcp())
    second_app = create_app(settings, pi_runner=object(), mcp_provider=FastMcp())
    first_app.state.mcp.lease_seconds = 0.12
    second_app.state.mcp.lease_seconds = 0.12
    async with (  # noqa: SIM117 — keep server lifespans outside client lifetimes.
        first_app.router.lifespan_context(first_app),
        second_app.router.lifespan_context(second_app),
    ):
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=first_app), base_url="http://first"
            ) as first_client,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=second_app), base_url="http://second"
            ) as second_client,
        ):
            first_login = await first_client.post(
                "/api/v1/auth/demo", json={"email": "alice@example.org"}
            )
            second_login = await second_client.post(
                "/api/v1/auth/demo", json={"email": "alice@example.org"}
            )
            first_headers = {"Authorization": f"Bearer {first_login.json()['access_token']}"}
            second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
            url = "/api/v1/mcp/tools/slow_lookup/invoke"
            first_call = asyncio.create_task(first_client.post(url, json={}, headers=first_headers))
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.sleep(0.18)  # A live call must renew its lease before admission expires.
            busy = await second_client.post(url, json={}, headers=second_headers)
            assert busy.status_code == 429
            assert busy.json()["detail"]["code"] == "MCP_CAPACITY_EXCEEDED"
            assert second_calls == 0
            release.set()
            assert (await asyncio.wait_for(first_call, timeout=1)).status_code == 200
            assert (
                await second_client.post(url, json={}, headers=second_headers)
            ).status_code == 200
            assert second_calls == 1


@pytest.mark.asyncio
async def test_mcp_renewal_survives_synchronous_identity_latency(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    second_calls = 0

    class SlowMcp:
        def tools(self):
            return [
                McpTool(name="slow_lookup", description="Lookup", input_schema={"type": "object"})
            ]

        async def discover(self):
            return self.tools()

        async def invoke(self, name, arguments):
            started.set()
            await release.wait()
            return McpInvokeResult(tool=name, result={"ok": True})

    class FastMcp(SlowMcp):
        async def invoke(self, name, arguments):
            nonlocal second_calls
            second_calls += 1
            return McpInvokeResult(tool=name, result={"ok": True})

    settings = Settings(
        agent_runtime="pi",
        agent_db_path=str(tmp_path / "capacity.sqlite3"),
        mcp_executor="remote",
        mcp_max_concurrent_calls=1,
        mcp_queue_timeout_seconds=0.05,
    )
    first = create_app(settings, pi_runner=object(), mcp_provider=SlowMcp())
    second = create_app(settings, pi_runner=object(), mcp_provider=FastMcp())
    first.state.mcp.lease_seconds = second.state.mcp.lease_seconds = 0.12
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://first") as one,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second), base_url="http://second"
        ) as two,
    ):
        identity = (await one.post("/api/v1/auth/demo", json={"email": "user@example.org"})).json()
        second_identity = (
            await two.post("/api/v1/auth/demo", json={"email": "user@example.org"})
        ).json()
        original_verify = second.state.identity_provider.verify

        async def slow_identity(token):
            # Deliberately block the event loop to reproduce synchronous identity latency.
            time.sleep(0.25)  # noqa: ASYNC251
            return await original_verify(token)

        second.state.identity_provider.verify = slow_identity
        url = "/api/v1/mcp/tools/slow_lookup/invoke"
        pending = asyncio.create_task(
            one.post(url, json={}, headers={"Authorization": f"Bearer {identity['access_token']}"})
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        try:
            busy = await two.post(
                url, json={}, headers={"Authorization": f"Bearer {second_identity['access_token']}"}
            )
            assert busy.status_code == 429
            assert second_calls == 0
        finally:
            release.set()
            await pending


@pytest.mark.asyncio
async def test_expired_mcp_lease_does_not_block_a_new_instance(tmp_path):
    class FastMcp:
        def tools(self):
            return [McpTool(name="lookup", description="Lookup", input_schema={"type": "object"})]

        async def discover(self):
            return self.tools()

        async def invoke(self, name, arguments):
            return McpInvokeResult(tool=name, result={"ok": True})

    path = str(tmp_path / "shared.sqlite3")
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=path,
            mcp_executor="remote",
            mcp_max_concurrent_calls=1,
            mcp_queue_timeout_seconds=0.05,
        ),
        pi_runner=object(),
        mcp_provider=FastMcp(),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO mcp_execution_leases VALUES (?,?)", ("crashed-worker", time.time() - 1)
        )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.post("/api/v1/mcp/tools/lookup/invoke", json={}, headers=headers)
    assert response.status_code == 200


def test_mcp_execution_capacity_requires_positive_limits():
    with pytest.raises(ValueError, match="MCP_MAX_CONCURRENT_CALLS"):
        create_app(Settings(mcp_max_concurrent_calls=0))
    with pytest.raises(ValueError, match="MCP_QUEUE_TIMEOUT_SECONDS"):
        create_app(Settings(mcp_queue_timeout_seconds=0))


def test_shared_mcp_instances_reject_conflicting_capacity_limits(tmp_path):
    class EmptyMcp:
        def tools(self):
            return []

        async def discover(self):
            return []

        async def invoke(self, name, arguments):
            return None

    path = str(tmp_path / "shared.sqlite3")
    create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=path,
            mcp_executor="remote",
            mcp_max_concurrent_calls=1,
        ),
        pi_runner=object(),
        mcp_provider=EmptyMcp(),
    )
    with pytest.raises(ValueError, match="MCP capacity must match"):
        create_app(
            Settings(
                agent_runtime="pi",
                agent_db_path=path,
                mcp_executor="remote",
                mcp_max_concurrent_calls=2,
            ),
            pi_runner=object(),
            mcp_provider=EmptyMcp(),
        )
