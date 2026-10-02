from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.main import create_app


@pytest.mark.asyncio
async def test_guest_af3_and_mcp_are_denied_before_any_job_or_tool_run():
    app = create_app(Settings())
    token, guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        af3 = await client.post("/api/v1/af3/jobs", headers=headers,
                                json={"estimated_gpu_minutes": 5})
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
        resources = await client.get("/api/v1/resources", headers=headers)
        skills = await client.get("/api/v1/skills", headers=headers)
        invocation = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
            json={"query": "RNA"},
        )

    assert af3.status_code == 403
    assert af3.json()["detail"]["code"] == "LOGIN_REQUIRED"
    assert tools.json() == []
    assert resources.json() == []
    assert skills.json() == []
    assert invocation.status_code == 403
    assert invocation.json()["detail"]["code"] == "TOOL_NOT_ALLOWED"
    assert app.state.quotas.usage_for(guest.id).gpu.reserved == 0
    assert app.state.tool_runs.list_for(guest.id) == []


@pytest.mark.asyncio
async def test_member_keeps_af3_and_mcp_access():
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        signed_in = await client.post("/api/v1/auth/demo",
                                      json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
        invoked = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
            json={"query": "RNA"},
        )
        af3 = await client.post("/api/v1/af3/jobs", headers=headers,
                                json={"estimated_gpu_minutes": 5})

    assert {tool["name"] for tool in tools.json()} >= {"search_pdb"}
    assert invoked.status_code == 200
    assert af3.status_code == 200


@pytest.mark.asyncio
async def test_operator_can_allow_one_guest_mcp_tool_without_exposing_others():
    app = create_app(Settings(guest_mcp_allowed_tools_json='["search_pdb"]'))
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        tools = await client.get("/api/v1/mcp/tools", headers=headers)
        allowed = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
            json={"query": "RNA"},
        )
        denied = await client.post(
            "/api/v1/mcp/tools/fetch_uniprot/invoke", headers=headers,
            json={"accession": "P12345"},
        )

    assert [tool["name"] for tool in tools.json()] == ["search_pdb"]
    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_guest_mcp_allowlist_cannot_enable_af3():
    with pytest.raises(ValueError):
        create_app(Settings(guest_mcp_allowed_tools_json='["submit_af3"]'))


def test_old_pi_context_cannot_reintroduce_guest_mcp_tool(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    user_id = "guest-1"
    app.state.identity_policy.observe_verified_user(user_id, True)
    store = app.state.conversations
    project_id = store.project_for(user_id).id
    session_id = store.create_session(user_id, project_id, "Research").id
    run_id = store.send_message(user_id, session_id,
                                MessageRequest(content="Search")).run_id
    store.set_run_context(run_id, "", ("search_pdb", "fetch_uniprot"))

    environment = app.state.agent_service._environment(user_id, run_id)

    assert environment["PSKIT_MCP_TOOLS_JSON"] == "[]"
    assert environment["PSKIT_AF3_ENABLED"] == "0"


@pytest.mark.asyncio
async def test_pi_internal_af3_mcp_and_old_approval_reject_guest(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    token, guest = app.state.demo_store.issue_anonymous_token()
    app.state.identity_policy.observe_verified_user(guest.id, True)
    store = app.state.conversations
    project_id = store.project_for(guest.id).id
    session_id = store.create_session(guest.id, project_id, "Research").id
    run_id = store.send_message(guest.id, session_id,
                                MessageRequest(content="Analyze")).run_id
    assert store.claim_initial_run(run_id)
    store.set_run_context(run_id, "", ("search_pdb",))
    approval = store.request_af3_approval(guest.id, run_id, "old-call", 30)
    tool_headers = {"Authorization": f"Bearer {app.state.agent_service.tool_token(run_id)}"}
    user_headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        immediate = await client.post("/internal/af3/jobs", headers=tool_headers,
                                      json={"run_id": run_id, "tool_call_id": "new-call",
                                            "estimated_gpu_minutes": 5})
        approval_request = await client.post("/internal/af3/jobs", headers=tool_headers,
                                             json={"run_id": run_id,
                                                   "tool_call_id": "new-high-call",
                                                   "estimated_gpu_minutes": 30})
        mcp = await client.post(
            "/internal/mcp/tools/search_pdb/invoke", headers=tool_headers,
            json={"run_id": run_id, "tool_call_id": "mcp-call",
                  "arguments": {"query": "RNA"}},
        )
        resume_fetch = await client.get(
            "/internal/af3/jobs/legacy-job", headers=tool_headers,
            params={"run_id": run_id},
        )
        decision = await client.post(
            f"/api/v1/runs/{run_id}/approvals/{approval.approval_id}",
            headers=user_headers, json={"decision": "approved"},
        )

    assert [response.status_code for response in
            (immediate, approval_request, resume_fetch, decision)] == [403, 403, 403, 403]
    assert all(response.json()["detail"]["code"] == "LOGIN_REQUIRED"
               for response in (immediate, approval_request, resume_fetch, decision))
    assert mcp.status_code == 403
    assert mcp.json()["detail"]["code"] == "TOOL_NOT_ALLOWED"
    assert store.usage_for(guest.id).gpu.reserved == 0
    assert store.db.execute("SELECT COUNT(*) FROM agent_jobs WHERE user_id=?",
                            (guest.id,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_legacy_guest_af3_wakeup_stops_before_pi_and_token_reservation(tmp_path):
    class NoPromptPi:
        calls = 0

        async def prompt(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("Guest AF3 must not resume")

    pi = NoPromptPi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=pi,
    )
    user_id = "guest-1"
    app.state.identity_policy.observe_verified_user(user_id, True)
    store = app.state.conversations
    project_id = store.project_for(user_id).id
    session_id = store.create_session(user_id, project_id, "Research").id
    run_id = store.send_message(user_id, session_id,
                                MessageRequest(content="Legacy AF3")).run_id
    service = app.state.agent_service
    with store.db:
        store.db.execute(
            "UPDATE agent_runs SET status='resume_queued',lease_owner=?,lease_expires_at=? "
            "WHERE id=?",
            (service._instance_id, (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
             run_id),
        )

    await service._resume(user_id, session_id, run_id, "legacy-job")

    assert pi.calls == 0
    assert store.usage_for(user_id).tokens.used == 0
    assert store.run_status_for(user_id, run_id).status == "failed"
    assert any(event.type == "run.failed" and event.data.code == "LOGIN_REQUIRED"
               for event in store.events_for(user_id, run_id, None))
