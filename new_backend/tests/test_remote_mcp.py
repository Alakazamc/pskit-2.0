import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.adapters.live.remote_mcp import RemoteMcp
from app.api.mcp_validation import validate_mcp_arguments
from app.config import Settings
from app.contracts.capabilities import McpTool
from app.main import create_app
from app.ports.providers import ProviderUnavailable


@pytest.mark.asyncio
async def test_remote_mcp_discovers_schema_and_invokes_over_streamable_http():
    server = FastMCP("test-lab", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))

    @server.tool()
    def lookup_protein(accession: str) -> dict[str, str]:
        return {"accession": accession, "name": "Test protein"}

    server_app = server.streamable_http_app()
    async with server.session_manager.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://localhost",
    ) as client:
        remote = RemoteMcp("http://localhost/mcp", allowed_tools={"lookup_protein"},
                           http_client=client)
        await remote.discover()
        tool = next(tool for tool in remote.tools() if tool.name == "lookup_protein")
        assert "accession" in tool.input_schema["required"]
        with pytest.raises(HTTPException) as invalid:
            validate_mcp_arguments(remote, "lookup_protein", {})
        assert invalid.value.status_code == 422
        result = await remote.invoke("lookup_protein", {"accession": "P12345"})
        assert result is not None
        assert result.result == {"accession": "P12345", "name": "Test protein"}


@pytest.mark.asyncio
async def test_remote_mcp_hides_tool_failure_details():
    server = FastMCP("test-lab", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))

    @server.tool()
    def failing_tool() -> str:
        raise RuntimeError("secret upstream traceback")

    server_app = server.streamable_http_app()
    async with server.session_manager.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://localhost",
    ) as client:
        remote = RemoteMcp("http://localhost/mcp", allowed_tools={"failing_tool"},
                           http_client=client)
        await remote.discover()
        with pytest.raises(Exception, match="MCP tool failed") as error:
            await remote.invoke("failing_tool", {})
        assert "secret upstream" not in str(error.value)


@pytest.mark.asyncio
async def test_remote_mcp_is_available_through_versioned_api_with_schema_validation():
    server = FastMCP("test-lab", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))

    @server.tool()
    def lookup_protein(accession: str) -> dict[str, str]:
        return {"accession": accession, "name": "Test protein"}

    server_app = server.streamable_http_app()
    async with server.session_manager.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app), base_url="http://localhost",
    ) as mcp_client:
        remote = RemoteMcp("http://localhost/mcp", allowed_tools={"lookup_protein"},
                           http_client=mcp_client)
        app = create_app(Settings(mcp_executor="remote", mock_af3_seconds=100),
                         mcp_provider=remote)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            tools = await client.get("/api/v1/mcp/tools", headers=headers)
            invalid = await client.post(
                "/api/v1/mcp/tools/lookup_protein/invoke", json={}, headers=headers,
            )
            result = await client.post(
                "/api/v1/mcp/tools/lookup_protein/invoke",
                json={"accession": "P12345"}, headers=headers,
            )
            resources = await client.get("/api/v1/resources", headers=headers)
    assert [tool["name"] for tool in tools.json()] == ["lookup_protein"]
    assert invalid.status_code == 422
    assert result.status_code == 200
    assert result.json()["result"] == {"accession": "P12345", "name": "Test protein"}
    assert [resource["id"] for resource in resources.json()] == ["lookup_protein"]


@pytest.mark.asyncio
async def test_remote_mcp_only_exposes_explicitly_allowed_tools():
    server = FastMCP("test-lab", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))

    @server.tool()
    def lookup_protein(accession: str) -> str:
        return accession

    @server.tool()
    def delete_data() -> str:
        return "deleted"

    server_app = server.streamable_http_app()
    async with server.session_manager.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app), base_url="http://localhost",
    ) as client:
        remote = RemoteMcp("http://localhost/mcp", http_client=client,
                           allowed_tools={"lookup_protein"})
        await remote.discover()
        assert [tool.name for tool in remote.tools()] == ["lookup_protein"]
        assert await remote.invoke("delete_data", {}) is None


@pytest.mark.asyncio
async def test_remote_mcp_rejects_oversized_results_before_persistence():
    server = FastMCP("test-lab", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))

    @server.tool()
    def huge_result() -> dict[str, str]:
        return {"data": "x" * (1024 * 1024 + 1)}

    server_app = server.streamable_http_app()
    async with server.session_manager.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app), base_url="http://localhost",
    ) as client:
        remote = RemoteMcp("http://localhost/mcp", http_client=client,
                           allowed_tools={"huge_result"})
        await remote.discover()
        with pytest.raises(ProviderUnavailable, match="MCP result exceeds limit"):
            await remote.invoke("huge_result", {})


def test_remote_mode_requires_an_allowlist_and_endpoint():
    with pytest.raises(ValueError, match="RESEARCH_AGENT_MCP_URL"):
        create_app(Settings(mcp_executor="remote"))
    with pytest.raises(ValueError, match="RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON"):
        create_app(Settings(mcp_executor="remote", mcp_url="http://localhost/mcp"))


@pytest.mark.asyncio
async def test_unavailable_remote_mcp_does_not_block_login_or_workspace():
    class UnavailableMcp:
        def tools(self):
            return []

        async def discover(self):
            raise ProviderUnavailable("MCP discovery failed")

    app = create_app(Settings(mcp_executor="remote"), mcp_provider=UnavailableMcp())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        projects = await client.get("/api/v1/g", headers=headers)
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
    assert login.status_code == projects.status_code == 200
    assert tools.status_code == 503
    assert tools.json()["detail"]["code"] == "MCP_UPSTREAM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_remote_mcp_recovers_and_refreshes_catalog_without_losing_files():
    class EventuallyAvailableMcp:
        available = False

        def __init__(self):
            self._tools = []

        def tools(self):
            return self._tools

        async def discover(self):
            if not self.available:
                raise ProviderUnavailable("MCP discovery failed")
            self._tools = [McpTool(name="lookup_protein", description="Look up proteins",
                                   input_schema={"type": "object"})]
            return self._tools

    remote = EventuallyAvailableMcp()
    app = create_app(Settings(mcp_executor="remote", mcp_refresh_seconds=0.02,
                              mock_af3_seconds=100), mcp_provider=remote)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        uploaded = await client.post("/api/v1/files", headers=headers,
                                     json={"name": "notes.txt", "size": 5, "content": "notes"})
        assert uploaded.status_code == 200
        assert (await client.get("/api/v1/mcp/tools", headers=headers)).status_code == 503
        remote.available = True
        for _ in range(100):
            tools = await client.get("/api/v1/mcp/tools", headers=headers)
            if tools.status_code == 200:
                break
            await asyncio.sleep(0.02)
        resources = await client.get("/api/v1/resources", headers=headers)
        files = await client.get("/api/v1/files", headers=headers)
    assert tools.status_code == 200
    assert [tool["name"] for tool in tools.json()] == ["lookup_protein"]
    assert [resource["id"] for resource in resources.json()] == ["lookup_protein"]
    assert [file["id"] for file in files.json()] == [uploaded.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [
    [],
    {"type": "object", "properties": {"value": {"$ref": "https://untrusted.example/schema"}}},
])
async def test_invalid_remote_tool_schema_is_reported_as_discovery_failure(schema):
    remote = RemoteMcp("http://localhost/mcp", allowed_tools={"lookup_protein"})

    @asynccontextmanager
    async def fake_session():
        class Session:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(
                    name="lookup_protein", description="bad schema", inputSchema=schema
                )])

        yield Session()

    remote._session = fake_session
    with pytest.raises(ProviderUnavailable, match="MCP discovery failed"):
        await remote.discover()
