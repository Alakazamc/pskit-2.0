"""Direct MCP and PDF admission cannot bypass the durable release gate."""

import httpx
import pytest

from app.adapters.mock.mcp import MockMcp
from app.config import Settings
from app.contracts.capabilities import McpInvokeResult
from app.db.release_admission_migrations import migrate_admission_sqlite
from app.domain.mcp_capacity import McpCapacityStore, PdfCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.release_admission import AdmissionClosed, DurableAdmissionGate
from app.main import create_app
from app.ports.providers import ProviderUnavailable


def bind(store):
    migrate_admission_sqlite(store.db)
    gate = DurableAdmissionGate(store.db, environment="staging", project="pskit-agent-staging",
                                qualification_sha256="a" * 64)
    gate.configure()
    store.admission_gate = gate
    return gate


def identity():
    return {
        "adapter_id": "synthetic-adapter", "qualification_sha256": "a" * 64,
        "owner_nonce": "b" * 64, "environment": "staging", "project": "pskit-agent-staging",
        "previous_manifest_sha256": "c" * 64, "target_manifest_sha256": "d" * 64,
        "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"],
    }


@pytest.mark.parametrize("store_type", [McpCapacityStore, PdfCapacityStore])
def test_capacity_denies_new_work_but_can_finish_existing_work(tmp_path, store_type):
    store = store_type(str(tmp_path / "capacity.sqlite3"), 2)
    gate = bind(store)
    token = store.claim(30)
    gate.enter(identity())
    before = store.db.execute(f"SELECT * FROM {store.lease_table}").fetchall()
    with pytest.raises(AdmissionClosed):
        store.claim(30)
    assert not store.db.in_transaction
    assert store.db.execute(f"SELECT * FROM {store.lease_table}").fetchall() == before
    assert store.renew(token, 30)
    store.release(token)
    gate.release(identity())
    assert store.claim(30) is not None
    store.db.close()


def test_tool_call_replays_ack_but_rolls_back_denied_new_claim(tmp_path):
    store = McpToolCallStore(str(tmp_path / "calls.sqlite3"))
    gate = bind(store)
    answer = McpInvokeResult(tool="synthetic", result={"value": "safe"})
    assert store.claim("run-1", "old", "synthetic", {}) == ("claimed", None)
    store.complete("run-1", "old", answer)
    gate.enter(identity())
    assert store.claim("run-1", "old", "synthetic", {}) == ("completed", answer)
    with pytest.raises(AdmissionClosed):
        store.claim("run-1", "new", "synthetic", {})
    assert not store.db.in_transaction
    assert store.db.execute("SELECT count(*) FROM mcp_tool_calls").fetchone()[0] == 1
    gate.release(identity())
    assert store.claim("run-1", "new", "synthetic", {}) == ("claimed", None)
    store.db.close()


class AdmissionClosingMcp(MockMcp):
    closed = True
    invoked = 0

    async def invoke(self, name, arguments):
        if self.closed:
            raise AdmissionClosed("Synthetic hold entered before provider dispatch")
        self.invoked += 1
        return await super().invoke(name, arguments)


@pytest.mark.asyncio
async def test_public_call_denied_before_dispatch_can_retry_without_unknown_outcome(tmp_path):
    provider = AdmissionClosingMcp()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     mcp_provider=provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}", "Idempotency-Key": "synthetic"}
        denied = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
                                   json={"query": "RNA"})
        assert denied.status_code == 503
        assert provider.invoked == 0
        assert app.state.mcp_tool_calls.db.execute("SELECT count(*) FROM mcp_tool_calls").fetchone()[0] == 0
        provider.closed = False
        retried = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
                                    json={"query": "RNA"})
        assert retried.status_code == 200
        assert provider.invoked == 1


@pytest.mark.asyncio
async def test_internal_call_denied_before_dispatch_can_retry_without_unknown_outcome(tmp_path):
    from test_mcp_idempotency import running_mcp_run

    provider = AdmissionClosingMcp()
    async with running_mcp_run(tmp_path, provider) as (client, headers, payload):
        denied = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
        assert denied.status_code == 503
        assert provider.invoked == 0
        provider.closed = False
        retried = await client.post("/internal/mcp/tools/search_pdb/invoke", headers=headers, json=payload)
        assert retried.status_code == 200
        assert provider.invoked == 1


@pytest.mark.asyncio
async def test_unkeyed_dispatched_mcp_failure_remains_visible_as_unknown(tmp_path):
    class UncertainMcp(MockMcp):
        async def invoke(self, _name, _arguments):
            raise ProviderUnavailable("Synthetic dispatched call has no acknowledged outcome")

    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     mcp_provider=UncertainMcp())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        auth = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        failed = await client.post("/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
                                   json={"query": "RNA"})
        assert failed.status_code == 502
        assert app.state.mcp_tool_calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("unknown",)]
