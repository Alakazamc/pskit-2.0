import httpx
import pytest

from app.adapters.live.supabase_auth import (
    EmailAlreadyInUse, InvalidCredentials, SupabaseIdentityAdapter, SupabaseSession,
)
from app.config import Settings
from app.contracts.models import UserIdentity
from app.main import create_app


@pytest.mark.asyncio
async def test_mock_guest_upgrades_same_identity_without_losing_data_or_usage(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        guest_session = (await client.post("/api/v1/auth/anonymous", json={})).json()
        guest_id = guest_session["user"]["id"]
        old_headers = {"Authorization": f"Bearer {guest_session['access_token']}"}
        project = (await client.get("/api/v1/g", headers=old_headers)).json()[0]
        uploaded = await client.post("/api/v1/files", headers=old_headers,
                                     json={"name": "note.md", "size": 5,
                                           "content": "hello"})
        app.state.conversations.charge_tokens(guest_id, 17)
        started = await client.post("/api/v1/auth/upgrade/email", headers=old_headers,
                                    json={"email": "new@example.org"})
        before = app.state.identity_policy.tier_for(guest_id)
        wrong = await client.post("/api/v1/auth/upgrade/email/verify",
                                  headers=old_headers,
                                  json={"email": "new@example.org", "token": "111111"})
        verified = await client.post("/api/v1/auth/upgrade/email/verify",
                                     headers=old_headers,
                                     json={"email": "new@example.org", "token": "000000"})
        new_headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
        projects = await client.get("/api/v1/g", headers=new_headers)
        files = await client.get("/api/v1/files", headers=new_headers)
        usage = await client.get("/api/v1/usage", headers=new_headers)
        refresh = await client.post("/api/v1/auth/refresh")
        old_token = await client.get("/api/v1/usage", headers=old_headers)

    assert started.status_code == 200 and started.json()["status"] == "check_email"
    assert before == "guest"
    assert wrong.status_code == 401
    assert verified.status_code == 200
    assert verified.json()["user"]["id"] == guest_id
    assert verified.json()["user"]["is_anonymous"] is False
    assert app.state.identity_policy.tier_for(guest_id) == "member"
    assert project["id"] in {item["id"] for item in projects.json()}
    assert uploaded.json()["id"] in {item["id"] for item in files.json()}
    assert usage.json()["tokens"]["used"] == 17
    assert refresh.status_code == 200 and refresh.json()["user"]["id"] == guest_id
    assert old_token.status_code == 401


@pytest.mark.asyncio
async def test_mock_occupied_email_and_cross_guest_verification_do_not_merge_accounts():
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        await client.post("/api/v1/auth/demo", json={"email": "taken@example.org"})
        first = (await client.post("/api/v1/auth/anonymous", json={})).json()
        client.cookies.clear()
        second = (await client.post("/api/v1/auth/anonymous", json={})).json()
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        occupied = await client.post("/api/v1/auth/upgrade/email",
                                     headers=first_headers,
                                     json={"email": "taken@example.org"})
        started = await client.post("/api/v1/auth/upgrade/email", headers=first_headers,
                                    json={"email": "new@example.org"})
        crossed = await client.post("/api/v1/auth/upgrade/email/verify",
                                    headers=second_headers,
                                    json={"email": "new@example.org", "token": "000000"})

    assert occupied.status_code == 409
    assert occupied.json()["detail"]["code"] == "EMAIL_ALREADY_IN_USE"
    assert started.status_code == 200
    assert crossed.status_code == 409
    assert crossed.json()["detail"]["code"] == "UPGRADE_SESSION_MISMATCH"
    assert app.state.identity_policy.tier_for(first["user"]["id"]) == "guest"
    assert app.state.identity_policy.tier_for(second["user"]["id"]) == "guest"


@pytest.mark.asyncio
async def test_live_email_upgrade_rejects_verified_session_for_different_user(tmp_path):
    settings = Settings(mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
                        supabase_url="https://example.supabase.co",
                        supabase_publishable_key="publishable-test")
    app = create_app(settings)

    class SwappedIdentity:
        async def verify(self, token):
            if token == "guest-token":
                return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)
            return UserIdentity(id="someone-else", email="new@example.org",
                                name="Other", is_anonymous=False)

        async def update_guest_email(self, access_token, email):
            assert access_token == "guest-token" and email == "new@example.org"

        async def verify_otp(self, email, token, kind):
            assert kind == "email_change"
            return SupabaseSession("other-token", "other-refresh", 3600)

    app.state.identity_provider = SwappedIdentity()
    headers = {"Authorization": "Bearer guest-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        client.cookies.set("research_refresh_token", "guest-refresh", path="/api/v1/auth")
        started = await client.post("/api/v1/auth/upgrade/email", headers=headers,
                                    json={"email": "new@example.org"})
        verified = await client.post("/api/v1/auth/upgrade/email/verify", headers=headers,
                                     json={"email": "new@example.org", "token": "123456"})

    assert started.status_code == 200
    assert verified.status_code == 409
    assert verified.json()["detail"]["code"] == "UPGRADE_IDENTITY_MISMATCH"
    assert app.state.identity_policy.tier_for("guest-1") == "guest"
    assert client.cookies.get("research_refresh_token") == "guest-refresh"


@pytest.mark.asyncio
async def test_supabase_email_upgrade_uses_authenticated_user_update_and_email_change_otp():
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "guest-1", "is_anonymous": True})
        return httpx.Response(200, json={"access_token": "member-token",
                                          "refresh_token": "member-refresh",
                                          "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = SupabaseIdentityAdapter("https://example.supabase.co", "key", client)
        await adapter.update_guest_email("guest-token", "new@example.org")
        session = await adapter.verify_otp("new@example.org", "123456", "email_change")

    assert session.access_token == "member-token"
    assert calls[0].method == "PUT" and calls[0].url.path == "/auth/v1/user"
    assert calls[0].headers["authorization"] == "Bearer guest-token"
    assert calls[0].content == b'{"email":"new@example.org"}'
    assert calls[1].method == "POST" and calls[1].url.path == "/auth/v1/verify"
    assert b'"type":"email_change"' in calls[1].content


@pytest.mark.asyncio
async def test_live_upgrade_can_finish_on_another_python_instance_with_same_user_id(tmp_path):
    settings = Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example/v1",
        model_gateway_model="research-model",
    )

    class Identity:
        async def verify(self, token):
            if token == "guest-token":
                return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)
            if token == "member-token":
                return UserIdentity(id="guest-1", email="new@example.org",
                                    name="New", is_anonymous=False)
            return None

        async def update_guest_email(self, access_token, email):
            assert access_token == "guest-token" and email == "new@example.org"

        async def verify_otp(self, email, token, kind):
            if token != "123456":
                raise InvalidCredentials
            assert email == "new@example.org" and kind == "email_change"
            return SupabaseSession("member-token", "member-refresh", 3600)

    first = create_app(settings, pi_runner=object())
    first.state.identity_provider = Identity()
    guest_headers = {"Authorization": "Bearer guest-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),
                                 base_url="http://first") as client:
        project = (await client.get("/api/v1/g", headers=guest_headers)).json()[0]
        file = await client.post("/api/v1/files", headers=guest_headers,
                                 json={"name": "note.md", "size": 5,
                                       "content": "hello"})
        first.state.conversations.charge_tokens("guest-1", 17)
        started = await client.post("/api/v1/auth/upgrade/email", headers=guest_headers,
                                    json={"email": "new@example.org"})

    second = create_app(settings, pi_runner=object())
    second.state.identity_provider = Identity()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),
                                 base_url="http://second") as client:
        wrong = await client.post("/api/v1/auth/upgrade/email/verify",
                                  headers=guest_headers,
                                  json={"email": "new@example.org", "token": "000000"})
        verified = await client.post("/api/v1/auth/upgrade/email/verify",
                                     headers=guest_headers,
                                     json={"email": "new@example.org", "token": "123456"})
        member_headers = {"Authorization": "Bearer member-token"}
        projects = await client.get("/api/v1/g", headers=member_headers)
        files = await client.get("/api/v1/files", headers=member_headers)
        usage = await client.get("/api/v1/usage", headers=member_headers)

    assert started.status_code == 200
    assert wrong.status_code == 401
    assert verified.status_code == 200
    assert verified.json()["user"]["id"] == "guest-1"
    assert verified.json()["user"]["is_anonymous"] is False
    assert second.state.identity_policy.tier_for("guest-1") == "member"
    assert project["id"] in {item["id"] for item in projects.json()}
    assert file.json()["id"] in {item["id"] for item in files.json()}
    assert usage.json()["tokens"]["used"] == 17


@pytest.mark.asyncio
async def test_supabase_occupied_email_error_has_distinct_adapter_exception():
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"code": "email_exists"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = SupabaseIdentityAdapter("https://example.supabase.co", "key", client)
        with pytest.raises(EmailAlreadyInUse):
            await adapter.update_guest_email("guest-token", "taken@example.org")
