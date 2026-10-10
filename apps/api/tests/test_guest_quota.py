from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.domain.quota import TokenQuotaExceeded
from app.main import create_app


@pytest.mark.asyncio
async def test_guest_usage_has_monthly_token_and_zero_daily_gpu_defaults(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    token, guest = app.state.demo_store.issue_anonymous_token()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/api/v1/usage", headers={
            "Authorization": f"Bearer {token}",
        })

    assert response.status_code == 200
    assert app.state.identity_policy.tier_for(guest.id) == "guest"
    assert response.json()["tokens"]["limit"] == 20_000
    assert response.json()["gpu"]["limit"] == 0


@pytest.mark.asyncio
async def test_mock_guest_chat_uses_configured_token_limit_at_admission():
    app = create_app(Settings(guest_monthly_token_limit=0))
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        session = await client.post("/api/v1/c", json={"title": "Research"}, headers=headers)
        sent = await client.post(
            f"/api/v1/c/{session.json()['id']}/messages", json={"content": "Hello"},
            headers=headers,
        )
        usage = await client.get("/api/v1/usage", headers=headers)

    assert session.status_code == 201
    assert sent.status_code == 409
    assert sent.json()["detail"]["code"] == "TOKEN_QUOTA_EXCEEDED"
    assert usage.json()["tokens"]["limit"] == 0


@pytest.mark.asyncio
async def test_pi_guest_chat_admission_uses_persistent_guest_limit(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                 guest_monthly_token_limit=0),
        pi_runner=object(),
    )

    class NoopAgent:
        def has_model_token(self, user_id: str) -> bool:
            return True

        def schedule(self, user_id: str, session_id: str, run_id: str, content: str) -> None:
            pass

    app.state.agent_service = NoopAgent()
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        session = await client.post("/api/v1/c", json={"title": "Research"}, headers=headers)
        sent = await client.post(
            f"/api/v1/c/{session.json()['id']}/messages", json={"content": "Hello"},
            headers=headers,
        )
        usage = await client.get("/api/v1/usage", headers=headers)

    assert session.status_code == 201
    assert sent.status_code == 409
    assert sent.json()["detail"]["code"] == "TOKEN_QUOTA_EXCEEDED"
    assert usage.json()["tokens"]["limit"] == 0


def test_background_token_charge_cannot_bypass_guest_limit(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                 guest_monthly_token_limit=0),
        pi_runner=object(),
    )
    app.state.identity_policy.observe_verified_user("guest-1", True)

    with pytest.raises(TokenQuotaExceeded):
        app.state.conversations.charge_tokens("guest-1", 1)

    assert app.state.conversations.usage_for("guest-1").tokens.used == 0


def test_af3_resume_cannot_reserve_more_tokens_than_guest_has(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                 guest_monthly_token_limit=0),
        pi_runner=object(),
    )
    app.state.identity_policy.observe_verified_user("guest-1", True)
    store = app.state.conversations
    with store.db:
        store.db.execute(
            "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) "
            "VALUES (?,?,?,?,?)",
            ("run-1", "guest-1", "session-1", "resume_queued", datetime.now(UTC).isoformat()),
        )

    with pytest.raises(TokenQuotaExceeded):
        store.reserve_resume_tokens("guest-1", "run-1", 1)

    assert store.usage_for("guest-1").tokens.used == 0


def test_model_proxy_preflight_respects_guest_limit(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                 guest_monthly_token_limit=100),
        pi_runner=object(),
    )
    app.state.identity_policy.observe_verified_user("guest-1", True)
    store = app.state.conversations
    with store.db:
        store.db.execute(
            "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) "
            "VALUES (?,?,?,?,?)",
            ("run-1", "guest-1", "session-1", "running", datetime.now(UTC).isoformat()),
        )
    store.reserve_resume_tokens("guest-1", "run-1", 10)

    with pytest.raises(TokenQuotaExceeded):
        store.reserve_model_call("guest-1", "run-1", "call-1", 100, 100)

    assert store.usage_for("guest-1").tokens.used == 10


def test_guest_run_concurrency_limit_and_upgrade_keep_usage(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                 guest_max_active_runs=1),
        pi_runner=object(),
    )
    policy = app.state.identity_policy
    store = app.state.conversations
    user_id = "guest-1"
    policy.observe_verified_user(user_id, True)
    project_id = store.project_for(user_id).id
    sessions = [store.create_session(user_id, project_id, title).id
                for title in ("First", "Second")]
    runs = [store.send_message(user_id, session, MessageRequest(content="analyze")).run_id
            for session in sessions]
    store.charge_tokens(user_id, 17)

    assert store.claim_initial_run(runs[0], max_user_active=2)
    assert not store.claim_initial_run(runs[1], max_user_active=2)
    assert store.usage_for(user_id).tokens.used == 17
    policy.observe_verified_user(user_id, False)
    assert store.claim_initial_run(runs[1], max_user_active=2)
    usage = store.usage_for(user_id)
    assert usage.tokens.limit == 1_000_000
    assert usage.tokens.used == 17


def test_admin_quota_override_precedes_guest_default(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    user_id = "guest-1"
    app.state.identity_policy.observe_verified_user(user_id, True)
    store = app.state.conversations
    store.set_token_limit(user_id, 37)
    store.set_gpu_limit(user_id, 5)

    usage = store.usage_for(user_id)
    assert usage.tokens.limit == 37
    assert usage.gpu.limit == 5


@pytest.mark.parametrize("setting,value", [
    ("guest_monthly_token_limit", -1),
    ("guest_daily_gpu_minute_limit", -1),
    ("member_monthly_token_limit", -1),
    ("member_daily_gpu_minute_limit", -1),
    ("guest_max_active_runs", 0),
])
def test_invalid_account_quota_settings_are_rejected(setting, value):
    with pytest.raises(ValueError):
        create_app(Settings(**{setting: value}))
