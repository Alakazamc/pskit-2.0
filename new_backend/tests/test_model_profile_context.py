"""Only server-selected runtime profiles enter durable Run context and Pi env."""

import json

import pytest

from app.contracts.conversation import MessageRequest
from app.contracts.model_runtime import ModelRuntimeProfile
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent import AgentService
from app.services.sandbox_sessions import pi_environment


def test_model_profile_is_pinned_for_idempotent_admission(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    profile = ModelRuntimeProfile(alias="ordinary-alias", revision=3).model_dump(mode="json")
    payload = MessageRequest(content="synthetic request")
    accepted, created = store.accept_message(
        "alice", "session-alice", payload, None, 10, "same-key", "same-hash",
        model_id="ordinary-alias", model_runtime_profile=profile,
    )
    assert created
    profile["revision"] = 4
    duplicate, created = store.accept_message(
        "alice", "session-alice", payload, None, 10, "same-key", "same-hash",
        model_id="ordinary-alias", model_runtime_profile=profile,
    )
    assert duplicate == accepted and not created
    pinned = store.run_context(accepted.run_id)["model_runtime_profile"]
    assert pinned["revision"] == 3
    assert pinned["alias"] == "ordinary-alias"
    store.db.close()


def test_agent_environment_uses_trusted_saved_profile(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    profile = ModelRuntimeProfile(alias="ordinary-alias", revision=2).model_dump(mode="json")
    accepted, _ = store.accept_message(
        "alice", "session-alice", MessageRequest(content="synthetic"), None, 10,
        None, "hash", model_id="ordinary-alias", model_runtime_profile=profile,
    )
    service = AgentService(store, None, "http://internal", 0, 1)
    env = service._environment("alice", accepted.run_id)
    assert json.loads(env["PSKIT_MODEL_RUNTIME_PROFILE"]) == profile
    assert env["PSKIT_MODEL_ID"] == "ordinary-alias"
    store.db.close()


def test_profile_environment_survives_scoped_sandbox_allowlist():
    profile = ModelRuntimeProfile(alias="alias", revision=1).model_dump_json()
    env = pi_environment({
        "PSKIT_MODEL_RUNTIME_PROFILE": profile,
        "PSKIT_RUN_ID": "run",
        "PSKIT_AGENT_TOOL_TOKEN": "token",
        "MODEL_GATEWAY_API_KEY": "run.token",
        "UNTRUSTED_PROFILE": "discard",
    })
    assert env["PSKIT_MODEL_RUNTIME_PROFILE"] == profile
    assert "UNTRUSTED_PROFILE" not in env


@pytest.mark.asyncio
async def test_request_profile_cannot_replace_server_selection(tmp_path):
    import httpx

    from app.config import Settings
    from app.main import create_app
    from app.services.model_catalog import ModelOption

    profile = ModelRuntimeProfile(alias="trusted-alias", revision=5)

    class Catalog:
        async def resolve(self, requested):
            return ModelOption(id="trusted-alias", runtime_profile=profile)

    class Pi:
        async def prompt(self, *args, **kwargs):
            return {"text": "ok"}

    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "api.sqlite3")),
                     pi_runner=Pi())
    app.state.model_catalog = Catalog()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://backend") as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        project = (await client.get("/api/v1/g", headers=headers)).json()[0]
        session_id = project["id"].replace("project-", "session-")
        response = await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={
            "content": "synthetic", "model": "trusted-alias",
            "model_runtime_profile": {"alias": "forged", "revision": 999},
        })
    assert response.status_code == 200
    context = app.state.conversations.run_context(response.json()["run_id"])
    assert context["model_runtime_profile"] == profile.model_dump(mode="json")
    await app.state.agent_service.stop()
    app.state.conversations.db.close()
