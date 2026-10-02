import asyncio
import json

import httpx
import pytest

import app.main as main_module
from app.config import Settings
from app.contracts.capabilities import McpInvokeResult, McpTool
from app.main import create_app
from app.ports.providers import ProviderUnavailable


@pytest.mark.asyncio
async def test_multiple_mcp_servers_are_namespaced_and_fail_independently(tmp_path, monkeypatch):
    servers = {}

    class FakeRemote:
        def __init__(self, url, *, allowed_tools, bearer_token="", timeout_seconds=30):
            self.url = url
            self.allowed_tools = allowed_tools
            self.bearer_token = bearer_token
            self.available = True
            self.calls = []
            servers[url] = self

        def tools(self):
            return []

        async def discover(self):
            if not self.available:
                raise ProviderUnavailable("MCP discovery failed")
            return [McpTool(name="lookup", description="Lookup", input_schema={
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"], "additionalProperties": False,
            })]

        async def invoke(self, name, arguments):
            self.calls.append((name, arguments))
            return McpInvokeResult(tool=name, result={"server": self.url, "query": arguments["query"]})

    monkeypatch.setattr(main_module, "RemoteMcp", FakeRemote)
    monkeypatch.setenv("PSKIT_PDB_MCP_TOKEN", "server-secret")
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        mcp_executor="remote", mcp_refresh_seconds=0.02,
        admin_api_key="private-admin",
        mcp_servers_json=json.dumps([
            {"id": "pdb", "url": "https://pdb.example/mcp", "allowed_tools": ["lookup"],
             "bearer_token_env": "PSKIT_PDB_MCP_TOKEN"},
            {"id": "uniprot", "url": "https://uniprot.example/mcp", "allowed_tools": ["lookup"]},
        ]),
    )
    app = create_app(settings, pi_runner=object())
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            login = (await client.post("/api/v1/auth/demo",
                                       json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            initial = await client.get("/api/v1/mcp/tools", headers=headers)
            pdb = await client.post("/api/v1/mcp/tools/pdb__lookup/invoke", headers=headers,
                                    json={"query": "RNA"})
            servers["https://pdb.example/mcp"].available = False
            for _ in range(100):
                tools = await client.get("/api/v1/mcp/tools", headers=headers)
                if [tool["name"] for tool in tools.json()] == ["uniprot__lookup"]:
                    break
                await asyncio.sleep(0.02)
            still_works = await client.post("/api/v1/mcp/tools/uniprot__lookup/invoke",
                                            headers=headers, json={"query": "P53"})
            denied = await client.post("/api/v1/mcp/tools/pdb__lookup/invoke",
                                       headers=headers, json={"query": "RNA"})
            health = await client.get("/api/v1/admin/dependencies",
                                      headers={"X-Admin-Key": "private-admin"})

    assert initial.status_code == 200
    assert [tool["name"] for tool in initial.json()] == ["pdb__lookup", "uniprot__lookup"]
    assert pdb.json()["tool"] == "pdb__lookup"
    assert servers["https://pdb.example/mcp"].bearer_token == "server-secret"
    assert servers["https://pdb.example/mcp"].calls == [("lookup", {"query": "RNA"})]
    assert still_works.status_code == 200 and still_works.json()["tool"] == "uniprot__lookup"
    assert denied.status_code in {403, 404}
    assert health.status_code == 200
    assert health.json()["mcp"] == "partial"
    assert health.json()["mcp_servers"] == {
        "pdb": "unavailable", "uniprot": "available",
    }


@pytest.mark.parametrize("servers", [
    [{"id": "pdb", "url": "https://example/mcp", "allowed_tools": ["lookup"]},
     {"id": "pdb", "url": "https://other/mcp", "allowed_tools": ["lookup"]}],
    [{"id": "bad-name", "url": "https://example/mcp", "allowed_tools": ["lookup"]}],
    [{"id": "pdb", "url": "https://example/mcp", "allowed_tools": []}],
])
def test_multi_mcp_configuration_rejects_collisions_and_invalid_names(servers):
    with pytest.raises(ValueError, match="MCP_SERVERS_JSON"):
        create_app(Settings(mcp_executor="remote", mcp_servers_json=json.dumps(servers)))


def test_multi_mcp_fails_closed_when_named_credential_is_missing(monkeypatch):
    monkeypatch.delenv("PSKIT_ABSENT_MCP_TOKEN", raising=False)
    servers = [{"id": "pdb", "url": "https://example/mcp", "allowed_tools": ["lookup"],
                "bearer_token_env": "PSKIT_ABSENT_MCP_TOKEN"}]
    with pytest.raises(ValueError, match="PSKIT_ABSENT_MCP_TOKEN"):
        create_app(Settings(mcp_executor="remote", mcp_servers_json=json.dumps(servers)))
