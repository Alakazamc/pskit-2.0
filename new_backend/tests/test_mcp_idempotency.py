import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.adapters.mock.mcp import MockMcp
from app.config import Settings
from app.main import create_app
from app.ports.providers import ProviderUnavailable


class PausedPi:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.release = asyncio.Event()

    async def prompt(self, session_id, message, on_event, **kwargs):
        self.calls.append(kwargs)
        await self.release.wait()
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Done"}


class CountingMcp(MockMcp):
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, name: str, arguments: dict):
        self.calls += 1
        return await super().invoke(name, arguments)


@asynccontextmanager
async def running_mcp_run(tmp_path, mcp, settings=None):
    pi = PausedPi()
    app = create_app(
        settings or Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=pi, mcp_provider=mcp,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            sent = await client.post(
                f"/api/v1/c/{project_id.replace('project-', 'session-')}/messages",
                headers=headers,
                json={"content": "Search structures", "resources": [{"id": "search_pdb", "name": "PDB"}]},
            )
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)
            assert pi.calls
            tool_headers = {"Authorization": f"Bearer {pi.calls[0]['environment']['PSKIT_AGENT_TOOL_TOKEN']}"}
            payload = {"run_id": sent.json()["run_id"], "tool_call_id": "call-1", "arguments": {"query": "RNA"}}
            try:
                yield client, tool_headers, payload
            finally:
                pi.release.set()


@pytest.mark.asyncio
async def test_repeated_pi_tool_call_returns_saved_result_without_reinvoking_mcp(tmp_path):
    pi = PausedPi()
    mcp = CountingMcp()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=pi, mcp_provider=mcp,
    )
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
                headers = {"Authorization": f"Bearer {auth['access_token']}"}
                project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
                session_id = project_id.replace("project-", "session-")
                sent = await client.post(
                    f"/api/v1/c/{session_id}/messages", headers=headers,
                    json={"content": "Search structures", "resources": [{"id": "search_pdb", "name": "PDB"}]},
                )
                for _ in range(100):
                    if pi.calls:
                        break
                    await asyncio.sleep(0.01)
                assert pi.calls
                run_id = sent.json()["run_id"]
                internal_headers = {"Authorization": f"Bearer {pi.calls[0]['environment']['PSKIT_AGENT_TOOL_TOKEN']}"}
                payload = {"run_id": run_id, "tool_call_id": "call-1", "arguments": {"query": "RNA"}}
                first = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=internal_headers, json=payload)
                repeated = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=internal_headers, json=payload)
    finally:
        pi.release.set()

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_inflight_pi_tool_call_rejects_duplicate_without_starting_another_mcp_request(tmp_path):
    class SlowMcp(CountingMcp):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def invoke(self, name: str, arguments: dict):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return await MockMcp.invoke(self, name, arguments)

    mcp = SlowMcp()
    async with running_mcp_run(tmp_path, mcp) as (client, headers, payload):
        first = asyncio.create_task(client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload))
        await asyncio.wait_for(mcp.started.wait(), timeout=1)
        repeated = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
        mcp.release.set()
        completed = await asyncio.wait_for(first, timeout=1)
    assert completed.status_code == 200
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "MCP_TOOL_CALL_OUTCOME_UNKNOWN"
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_uncertain_mcp_failure_cannot_be_replayed_under_same_tool_call_id(tmp_path):
    class FailingMcp(CountingMcp):
        async def invoke(self, name: str, arguments: dict):
            self.calls += 1
            raise ProviderUnavailable("possibly executed")

    mcp = FailingMcp()
    async with running_mcp_run(tmp_path, mcp) as (client, headers, payload):
        failed = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
        repeated = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
    assert failed.status_code == 502
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "MCP_TOOL_CALL_OUTCOME_UNKNOWN"
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_pi_tool_call_id_cannot_be_reused_for_different_arguments(tmp_path):
    mcp = CountingMcp()
    async with running_mcp_run(tmp_path, mcp) as (client, headers, payload):
        first = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
        changed = await client.post(
            "/internal/mcp/tools/search_pdb/invoke", headers=headers,
            json={**payload, "arguments": {"query": "DNA"}},
        )
    assert first.status_code == 200
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "MCP_TOOL_CALL_CONFLICT"
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_mcp_capacity_rejection_allows_same_pi_call_to_retry_after_slot_opens(tmp_path):
    class BusyMcp(CountingMcp):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def discover(self):
            return self.tools()

        async def invoke(self, name: str, arguments: dict):
            if arguments.get("query") == "block":
                self.started.set()
                await self.release.wait()
            return await super().invoke(name, arguments)

    mcp = BusyMcp()
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        mcp_executor="remote", mcp_max_concurrent_calls=1, mcp_queue_timeout_seconds=0.02,
    )
    async with running_mcp_run(tmp_path, mcp, settings) as (client, tool_headers, payload):
        auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        user_headers = {"Authorization": f"Bearer {auth['access_token']}"}
        busy_call = asyncio.create_task(client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=user_headers, json={"query": "block"},
        ))
        await asyncio.wait_for(mcp.started.wait(), timeout=1)
        rejected = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=tool_headers, json=payload)
        mcp.release.set()
        assert (await asyncio.wait_for(busy_call, timeout=1)).status_code == 200
        retried = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=tool_headers, json=payload)
    assert rejected.status_code == 429
    assert retried.status_code == 200
    assert mcp.calls == 2


@pytest.mark.asyncio
async def test_pi_tool_token_and_saved_result_work_on_another_python_instance(tmp_path):
    pi = PausedPi()
    mcp = CountingMcp()
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "shared.sqlite3"))
    first_app = create_app(settings, pi_runner=pi, mcp_provider=mcp)
    async with first_app.router.lifespan_context(first_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url="http://first",
        ) as first_client:
            auth = (await first_client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            project_id = (await first_client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            sent = await first_client.post(
                f"/api/v1/c/{project_id.replace('project-', 'session-')}/messages",
                headers=headers,
                json={"content": "Search structures", "resources": [{"id": "search_pdb", "name": "PDB"}]},
            )
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)
            assert pi.calls
            tool_headers = {"Authorization": f"Bearer {pi.calls[0]['environment']['PSKIT_AGENT_TOOL_TOKEN']}"}
            payload = {"run_id": sent.json()["run_id"], "tool_call_id": "call-shared", "arguments": {"query": "RNA"}}
            first = await first_client.post("/internal/mcp/tools/search_pdb/invoke", headers=tool_headers, json=payload)
            second_app = create_app(settings, pi_runner=PausedPi(), mcp_provider=mcp)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=second_app), base_url="http://second",
            ) as second_client:
                repeated = await second_client.post(
                    "/internal/mcp/tools/search_pdb/invoke", headers=tool_headers, json=payload,
                )
        pi.release.set()

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_direct_mcp_retry_with_idempotency_key_keeps_one_tool_run(tmp_path):
    mcp = CountingMcp()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=PausedPi(), mcp_provider=mcp,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}", "Idempotency-Key": "direct-call-1"}
        first = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers, json={"query": "RNA"})
        repeated = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers, json={"query": "RNA"})
        tool_runs = await client.get("/api/v1/tool-runs", headers=headers)
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert [item["id"] for item in tool_runs.json()] == [first.json()["run_id"]]
    assert mcp.calls == 1
