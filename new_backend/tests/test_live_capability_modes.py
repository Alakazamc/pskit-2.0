import httpx
import pytest

from app.config import Settings
from app.contracts.models import UserIdentity
from app.main import create_app


class StubIdentity:
    async def verify(self, access_token: str):
        return UserIdentity(id="alice", email="alice@example.org", name="Alice")


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://identity.example", "https://user:secret@identity.example",
    "https://identity.example/auth?token=secret",
])
def test_live_mode_rejects_invalid_supabase_base_url(url, tmp_path):
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        create_app(Settings(
            mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
            supabase_url=url, supabase_publishable_key="publishable-test",
        ))


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://identity.example", "https://user:secret@identity.example",
    "https://identity.example/auth?token=secret",
])
def test_invalid_supabase_public_url_rejected(url, tmp_path):
    with pytest.raises(ValueError, match="SUPABASE_PUBLIC_URL"):
        create_app(Settings(
            mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
            supabase_url="http://api-gw:8000", supabase_public_url=url,
            supabase_publishable_key="publishable-test",
        ))


def test_supabase_public_url_reads_environment(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLIC_URL", "https://agent.bioailab.net")
    assert Settings.from_env().supabase_public_url == "https://agent.bioailab.net"


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://gateway.example/v1", "https://user:secret@gateway.example/v1",
    "https://gateway.example/v1?token=secret",
])
def test_live_pi_rejects_invalid_model_gateway_base_url(url, tmp_path):
    with pytest.raises(ValueError, match="MODEL_GATEWAY_BASE_URL"):
        create_app(Settings(
            mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
            supabase_url="https://identity.example", supabase_publishable_key="publishable-test",
            model_gateway_base_url=url, model_gateway_model="research-model",
        ), pi_runner=object())


def test_live_mode_accepts_local_self_hosted_http_endpoints():
    Settings(
        mode="live", agent_runtime="pi",
        supabase_url="http://127.0.0.1:54321", supabase_publishable_key="publishable-test",
        model_gateway_base_url="http://127.0.0.1:4000/v1", model_gateway_model="research-model",
    ).require_live_config()


def test_live_af3_callback_requires_an_explicit_gpu_memory_profile(tmp_path):
    with pytest.raises(ValueError, match="AF3_MIN_GPU_MEMORY_MB"):
        create_app(Settings(
            mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
            supabase_url="https://example.supabase.co",
            supabase_publishable_key="publishable-test",
            model_gateway_base_url="https://gateway.example.org/v1",
            model_gateway_model="research-model", model_gateway_api_key="server-key",
            af3_executor="callback", compute_callback_key="compute-key",
        ), pi_runner=object())


def test_pi_tool_environment_hides_af3_when_executor_is_disabled(tmp_path):
    app = create_app(Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example.org/v1", model_gateway_model="research-model",
    ), pi_runner=object())
    service = app.state.agent_service
    assert service._environment("alice", "run-1")["PSKIT_AF3_ENABLED"] == "0"


@pytest.mark.asyncio
async def test_live_mode_disables_undeployed_mcp_and_af3_by_default(tmp_path):
    app = create_app(Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    ))
    app.state.identity_provider = StubIdentity()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer signed-jwt"}
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
        skills = await client.get("/api/v1/skills", headers=headers)
        mcp = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
                                json={"query": "protein"})
        af3 = await client.post("/api/v1/af3/jobs", headers=headers,
                                json={"estimated_gpu_minutes": 20})

    assert tools.status_code == 200 and tools.json() == []
    assert skills.status_code == 200 and skills.json() == []
    assert mcp.status_code == 503 and mcp.json()["detail"]["code"] == "MCP_NOT_CONFIGURED"
    assert af3.status_code == 503 and af3.json()["detail"]["code"] == "AF3_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_live_mode_can_explicitly_use_mock_capabilities_for_contract_development(tmp_path):
    app = create_app(Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        mcp_executor="mock", af3_executor="mock",
    ))
    app.state.identity_provider = StubIdentity()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer signed-jwt"}
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
        job = await client.post("/api/v1/af3/jobs", headers=headers,
                                json={"estimated_gpu_minutes": 20})

    assert tools.status_code == 200 and len(tools.json()) == 2
    assert job.status_code == 200 and job.json()["status"] == "queued"
