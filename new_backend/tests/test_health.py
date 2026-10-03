import asyncio
from time import monotonic

import httpx
import pytest

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.usefixtures("live_database")


@pytest.mark.asyncio
async def test_health_reports_configured_modes_without_secrets(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        model_gateway_api_key="private-secret", mcp_executor="disabled",
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200 and live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready", "identity": "mock", "agent": "pi",
        "mcp": "disabled", "af3": "mock", "external_services": "unchecked",
    }
    assert "private-secret" not in ready.text


@pytest.mark.asyncio
async def test_health_readiness_fails_when_persistent_store_is_unavailable(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    app.state.conversations.db.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "code": "DATABASE_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_dependency_diagnostics_require_admin_key_and_skip_network_in_mock_mode(tmp_path):
    app = create_app(Settings(
        agent_db_path=str(tmp_path / "agent.sqlite3"), admin_api_key="private-admin",
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/v1/admin/dependencies")
        forbidden = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "wrong"})
        checked = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "private-admin"})

    assert unauthorized.status_code == 403
    assert forbidden.status_code == 403
    assert checked.status_code == 200
    assert checked.json() == {
        "identity": "mock", "model_gateway": "disabled", "mcp": "mock", "af3": "mock",
        "mcp_servers": {},
    }
    assert "private-admin" not in checked.text

    no_key_app = create_app(Settings(agent_db_path=str(tmp_path / "no-key.sqlite3")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=no_key_app),
                                 base_url="http://test") as client:
        hidden = await client.get("/api/v1/admin/dependencies")
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_dependency_diagnostics_probe_live_identity_and_gateway_without_leaking_credentials(
    tmp_path, monkeypatch,
):
    from app.services import dependency_probe

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/auth/v1/health":
            return httpx.Response(200, json={"name": "Auth"})
        if request.url.path == "/v1/models":
            return httpx.Response(401, json={"error": "gateway-secret-is-invalid"})
        return httpx.Response(404)

    monkeypatch.setattr(dependency_probe, "make_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), timeout=2,
    ))
    app = create_app(Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://identity.example", supabase_publishable_key="private-publishable",
        model_gateway_base_url="https://gateway.example/v1",
        model_gateway_model="model-x", model_gateway_api_key="private-gateway-key",
        mcp_executor="disabled", af3_executor="disabled", admin_api_key="private-admin",
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        checked = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "private-admin"})

    assert checked.status_code == 200
    assert checked.json() == {
        "identity": "available", "model_gateway": "unauthorized", "mcp": "disabled",
        "af3": "disabled", "mcp_servers": {},
    }
    assert [str(request.url) for request in seen] == [
        "https://identity.example/auth/v1/health", "https://gateway.example/v1/models",
    ]
    assert seen[0].headers["apikey"] == "private-publishable"
    assert seen[1].headers["authorization"] == "Bearer private-gateway-key"
    assert "private" not in checked.text
    assert "gateway-secret-is-invalid" not in checked.text


@pytest.mark.asyncio
async def test_dependency_diagnostics_classify_transport_failure_and_rate_limit(tmp_path, monkeypatch):
    from app.services import dependency_probe

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/health":
            raise httpx.ConnectError("private upstream hostname", request=request)
        return httpx.Response(429, json={"error": "private gateway quota details"})

    monkeypatch.setattr(dependency_probe, "make_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), timeout=2,
    ))
    app = create_app(Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://identity.example", supabase_publishable_key="private-publishable",
        model_gateway_base_url="https://gateway.example/v1",
        model_gateway_model="model-x", model_gateway_api_key="private-gateway-key",
        mcp_executor="disabled", af3_executor="callback", compute_callback_key="private-compute",
        af3_min_gpu_memory_mb=40960, admin_api_key="private-admin",
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        checked = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "private-admin"})

    assert checked.status_code == 200
    assert checked.json() == {
        "identity": "unavailable", "model_gateway": "rate_limited", "mcp": "disabled",
        "af3": "inbound_only", "mcp_servers": {},
    }
    assert "private" not in checked.text


@pytest.mark.asyncio
async def test_dependency_diagnostics_have_a_wall_clock_deadline(tmp_path, monkeypatch):
    from app.services import dependency_probe

    async def slow_response(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    monkeypatch.setattr(dependency_probe, "PROBE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(dependency_probe, "make_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(slow_response), timeout=2,
    ))
    app = create_app(Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://identity.example", supabase_publishable_key="publishable",
        admin_api_key="admin-key",
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        checked = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "admin-key"})

    assert checked.status_code == 200
    assert checked.json()["identity"] == "unavailable"


@pytest.mark.asyncio
async def test_dependency_diagnostics_do_not_report_stale_mcp_discovery_as_available(tmp_path):
    class FakeMcp:
        def tools(self):
            return []

        async def discover(self):
            return []

        async def invoke(self, _name, _arguments):
            return None

    app = create_app(Settings(
        agent_db_path=str(tmp_path / "agent.sqlite3"), mcp_executor="remote",
        mcp_refresh_seconds=5, admin_api_key="admin-key",
    ), mcp_provider=FakeMcp())
    app.state.mcp_checked = True
    app.state.mcp_unavailable = False
    app.state.mcp_last_checked_at = monotonic() - 100
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        checked = await client.get("/api/v1/admin/dependencies", headers={"X-Admin-Key": "admin-key"})

    assert checked.status_code == 200
    assert checked.json()["mcp"] == "unknown"
