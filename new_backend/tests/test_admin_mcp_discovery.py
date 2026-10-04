"""Management discovery preserves actual MCP tools/list input and output schemas."""

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from app.config import Settings
from app.domain.admin.releases import ConfigReleaseService
from app.main import create_app


class Prediction(BaseModel):
    prediction: str


@pytest.mark.asyncio
@pytest.mark.parametrize("structured_output", [True, False])
async def test_mcp_schema_check_requires_actual_input_and_output(tmp_path, structured_output):
    server = FastMCP(
        "actual-lab",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    inference_calls = []

    @server.tool(structured_output=structured_output)
    def predict(sequence: str) -> Prediction:
        inference_calls.append(sequence)
        return Prediction(prediction=sequence)

    server_app = server.streamable_http_app()
    transport = httpx.ASGITransport(app=server_app)
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://localhost") as mcp_client,
    ):
        # Capture advertised schemas through the real MCP HTTP boundary.
        listed = await mcp_client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200
        tool = listed.json()["result"]["tools"][0]
        input_schema = tool["inputSchema"]
        output_schema = tool.get("outputSchema") or {
            "type": "object",
            "required": ["prediction"],
            "properties": {"prediction": {"type": "string"}},
        }
        app = create_app(Settings(agent_db_path=str(tmp_path / "mcp-admin.db")))
        app.state.admin_releases = ConfigReleaseService(
            app.state.admin_store,
            endpoints={
                "lab-mcp": {
                    "url": "http://localhost/mcp",
                    "transport": "mcp",
                    "allowed_tools": ["predict"],
                }
            },
            transport=transport,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = (
                await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
            ).json()
            uid = login["user"]["id"]
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            app.state.admin_store.grant(
                uid, "platform_admin", actor="bootstrap", reason="Admin grant"
            )
            response = await client.put(
                "/api/v1/admin/services/rna/draft",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "reason": "Review actual MCP schemas",
                    "name": "RNA",
                    "owner_user_id": uid,
                    "transport": "mcp",
                    "endpoint_ref": "lab-mcp",
                    "model_version": "v1",
                    "capabilities": [
                        {
                            "id": "rna.predict",
                            "version": "1",
                            "input_schema": input_schema,
                            "output_schema": output_schema,
                        }
                    ],
                },
            )
            assert response.status_code == 200
            checked = await client.post(
                "/api/v1/admin/services/rna/checks",
                headers=headers,
                json={
                    "expected_revision": 1,
                    "reason": "Compare actual MCP schemas",
                    "kind": "schema",
                },
            )
            assert checked.status_code == 200
            assert checked.json()["status"] == ("passed" if structured_output else "failed")
            service = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][
                0
            ]
            assert service["state"] == ("validated" if structured_output else "draft")
            if structured_output:
                assert (
                    checked.json()["discovered_capabilities"][0]["output_schema"]
                    == tool["outputSchema"]
                )
            assert inference_calls == []
