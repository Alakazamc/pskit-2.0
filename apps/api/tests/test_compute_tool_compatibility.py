"""New Run tool surface is pinned; historical wakeups keep legacy compatibility."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app
from app.services.agent import AgentService
from app.services.sandbox_sessions import pi_environment


def test_new_admitted_run_disables_generic_compute_without_changing_public_request(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    try:
        run, created = store.accept_message(
            "alice", "session-alice", MessageRequest(content="Echo document"),
            None, 1, "same", "hash",
        )
        assert created
        assert store.run_context(run.run_id)["compute_tool_compatibility"] is False
        service = AgentService(store, None, "http://internal", 0, 1)
        env = service._environment("alice", run.run_id)
        assert env["PSKIT_COMPUTE_COMPAT_ENABLED"] == "0"
    finally:
        store.db.close()


def test_explicit_compatibility_is_pinned_across_idempotent_retry(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    try:
        run, created = store.accept_message(
            "alice", "session-alice", MessageRequest(content="Echo document"),
            None, 1, "same", "hash", compute_tool_compatibility=True,
        )
        duplicate, duplicate_created = store.accept_message(
            "alice", "session-alice", MessageRequest(content="Echo document"),
            None, 1, "same", "hash", compute_tool_compatibility=False,
        )
        assert created and not duplicate_created and duplicate == run
        assert store.run_context(run.run_id)["compute_tool_compatibility"] is True
    finally:
        store.db.close()


@pytest.mark.parametrize("pinned,expected", [(True, "1"), (False, "0"), (None, "0"),
                                             ("true", "0"), (1, "0")])
def test_environment_uses_only_pinned_boolean_compatibility(tmp_path, pinned, expected):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    try:
        service = AgentService(store, None, "http://internal", 0, 1)
        env = service._environment("alice", "run", context={"compute_tool_compatibility": pinned})
        assert env["PSKIT_COMPUTE_COMPAT_ENABLED"] == expected
    finally:
        store.db.close()


def test_historical_run_missing_flag_still_exposes_legacy_tools_and_business_projection(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    try:
        run = store.send_message("alice", "session-alice", MessageRequest(content="historical"))
        service = AgentService(store, None, "http://internal", 0, 1)
        service.agent_compute_tools = SimpleNamespace(for_user=lambda user: [{"name": "echo"}])
        env = service._environment("alice", run.run_id)
        assert "compute_tool_compatibility" not in store.run_context(run.run_id)
        assert env["PSKIT_COMPUTE_COMPAT_ENABLED"] == "1"
        assert json.loads(env["PSKIT_COMPUTE_TOOLS_JSON"]) == [{"name": "echo"}]
    finally:
        store.db.close()


def test_scoped_sandbox_preserves_compatibility_flag():
    assert pi_environment({"PSKIT_COMPUTE_COMPAT_ENABLED": "0"})[
        "PSKIT_COMPUTE_COMPAT_ENABLED"
    ] == "0"


def test_compatibility_setting_is_explicit_and_defaults_off(monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_COMPUTE_TOOL_COMPATIBILITY_ENABLED", raising=False)
    assert Settings.from_env().compute_tool_compatibility_enabled is False
    monkeypatch.setenv("RESEARCH_AGENT_COMPUTE_TOOL_COMPATIBILITY_ENABLED", "true")
    assert Settings.from_env().compute_tool_compatibility_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_public_message_cannot_override_server_compatibility(tmp_path, enabled):
    class Pi:
        async def prompt(self, *args, **kwargs):
            return {"text": "ok"}

    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "api.sqlite3"),
        compute_tool_compatibility_enabled=enabled,
    ), pi_runner=Pi())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://backend") as client:
            login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            project = (await client.get("/api/v1/g", headers=headers)).json()[0]
            session_id = project["id"].replace("project-", "session-")
            response = await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={
                "content": "Echo document", "compute_tool_compatibility": not enabled,
            })
        assert response.status_code == 200
        context = app.state.conversations.run_context(response.json()["run_id"])
        assert context["compute_tool_compatibility"] is enabled
    finally:
        await app.state.agent_service.stop()
        app.state.conversations.db.close()
