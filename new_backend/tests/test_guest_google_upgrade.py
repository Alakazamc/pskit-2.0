from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.live.supabase_auth import SupabaseIdentityAdapter, SupabaseSession
from app.config import Settings
from app.contracts.models import UserIdentity
from app.main import create_app

pytestmark = pytest.mark.usefixtures("live_database")


async def _csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"Origin": "http://localhost:5174", "X-CSRF-Token": response.json()["csrf_token"]}


def _settings(tmp_path):
    return Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "auth.sqlite3"),
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example.org/v1",
        model_gateway_model="test-model",
        frontend_url="http://localhost:5174",
        public_api_url="http://localhost:18080",
    )


@pytest.mark.asyncio
async def test_guest_google_link_requires_guest_bearer(tmp_path):
    app = create_app(_settings(tmp_path))

    class Provider:
        async def verify(self, token):
            if token == "member-token":
                return UserIdentity(id="member-1", email="member@example.org",
                                    name="Member", is_anonymous=False)
            return None

    app.state.identity_provider = Provider()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        missing = await client.post("/api/v1/auth/upgrade/google/start")
        member = await client.post("/api/v1/auth/upgrade/google/start",
                                   headers={"Authorization": "Bearer member-token"})

    assert missing.status_code == 401
    assert member.status_code == 409
    assert member.json()["detail"]["code"] == "GUEST_REQUIRED"


@pytest.mark.asyncio
async def test_google_link_start_calls_supabase_manual_linking_endpoint_with_pkce():
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"url": "https://accounts.google.com/oauth"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = SupabaseIdentityAdapter("https://example.supabase.co", "key", client)
        url = await adapter.link_google_identity(
            "guest-token", "http://localhost:18080/api/v1/auth/google/callback?state=abc",
            "pkce-challenge",
        )

    assert url == "https://accounts.google.com/oauth"
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/auth/v1/user/identities/authorize"
    assert seen[0].headers["authorization"] == "Bearer guest-token"
    assert seen[0].url.params["provider"] == "google"
    assert seen[0].url.params["skip_http_redirect"] == "true"
    assert seen[0].url.params["code_challenge"] == "pkce-challenge"
    assert seen[0].url.params["code_challenge_method"] == "s256"


@pytest.mark.asyncio
async def test_guest_google_link_preserves_user_id_data_and_one_time_state_across_instances(tmp_path):
    settings = _settings(tmp_path)

    class Provider:
        callback = ""

        async def verify(self, token):
            if token == "guest-token":
                return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)
            if token == "linked-token":
                return UserIdentity(id="guest-1", email="linked@example.org",
                                    name="Linked", is_anonymous=False)
            return None

        async def link_google_identity(self, access_token, callback, challenge):
            assert access_token == "guest-token" and challenge
            self.callback = callback
            return "https://accounts.google.com/oauth"

        async def exchange_pkce(self, code, verifier):
            assert code == "google-code" and isinstance(verifier, str) and verifier
            return SupabaseSession("linked-token", "linked-refresh", 3600)

    provider = Provider()
    first = create_app(settings)
    first.state.identity_provider = provider
    guest_headers = {"Authorization": "Bearer guest-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),
                                 base_url="http://test") as client:
        project = (await client.get("/api/v1/g", headers=guest_headers)).json()[0]
        file = await client.post("/api/v1/files", headers=guest_headers,
                                 json={"name": "note.md", "size": 5,
                                       "content": "hello"})
        started = await client.post("/api/v1/auth/upgrade/google/start",
                                    headers=guest_headers)

    state = parse_qs(urlparse(provider.callback).query)["state"][0]
    second = create_app(settings)
    second.state.identity_provider = provider
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),
                                 base_url="http://test", follow_redirects=False) as client:
        client.cookies.set("research_oauth_state", state,
                           domain="test.local", path="/api/v1/auth/google/callback")
        client.cookies.set("research_refresh_token", "guest-refresh",
                           domain="test.local", path="/api/v1/auth")
        finished = await client.get("/api/v1/auth/google/callback",
                                    params={"state": state, "code": "google-code"})
        replay = await client.get("/api/v1/auth/google/callback",
                                  params={"state": state, "code": "google-code"})
        linked_headers = {"Authorization": "Bearer linked-token"}
        projects = await client.get("/api/v1/g", headers=linked_headers)
        files = await client.get("/api/v1/files", headers=linked_headers)

    assert started.status_code == 200
    assert started.json()["url"] == "https://accounts.google.com/oauth"
    assert finished.status_code == 303
    assert finished.headers["location"] == "http://localhost:5174/auth/callback"
    assert client.cookies.get("research_refresh_token") == "linked-refresh"
    assert replay.status_code == 400
    assert second.state.identity_policy.tier_for("guest-1") == "member"
    assert project["id"] in {item["id"] for item in projects.json()}
    assert file.json()["id"] in {item["id"] for item in files.json()}


@pytest.mark.asyncio
async def test_guest_google_link_identity_mismatch_keeps_guest_cookie_and_tier(tmp_path):
    settings = _settings(tmp_path)

    class Provider:
        callback = ""

        async def verify(self, token):
            if token == "guest-token":
                return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)
            return UserIdentity(id="different-1", email="taken@example.org",
                                name="Different", is_anonymous=False)

        async def link_google_identity(self, _access, callback, _challenge):
            self.callback = callback
            return "https://accounts.google.com/oauth"

        async def exchange_pkce(self, _code, _verifier):
            return SupabaseSession("different-token", "different-refresh", 3600)

    provider = Provider()
    app = create_app(settings)
    app.state.identity_provider = provider
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", follow_redirects=False) as client:
        started = await client.post("/api/v1/auth/upgrade/google/start",
                                    headers={"Authorization": "Bearer guest-token"})
        state = parse_qs(urlparse(provider.callback).query)["state"][0]
        client.cookies.set("research_oauth_state", state,
                           domain="test.local", path="/api/v1/auth/google/callback")
        client.cookies.set("research_refresh_token", "guest-refresh",
                           domain="test.local", path="/api/v1/auth")
        callback = await client.get("/api/v1/auth/google/callback",
                                    params={"state": state, "code": "google-code"})

    assert started.status_code == 200
    assert callback.status_code == 303
    assert callback.headers["location"].endswith("?error=GOOGLE_IDENTITY_CONFLICT")
    assert client.cookies.get("research_refresh_token") == "guest-refresh"
    assert app.state.identity_policy.tier_for("guest-1") == "guest"


@pytest.mark.asyncio
async def test_mock_guest_google_link_rotates_session_and_preserves_guest_data(tmp_path):
    app = create_app(Settings(agent_db_path=str(tmp_path / "mock.sqlite3")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", follow_redirects=False) as client:
        guest = (await client.post("/api/v1/auth/anonymous", json={})).json()
        headers = {"Authorization": f"Bearer {guest['access_token']}"}
        project = (await client.get("/api/v1/g", headers=headers)).json()[0]
        started = await client.post("/api/v1/auth/upgrade/google/start", headers=headers)
        callback_url = urlparse(started.json()["url"])
        callback = await client.get(f"{callback_url.path}?{callback_url.query}")
        refreshed = await client.post(
            "/api/v1/auth/refresh", headers=await _csrf_headers(client)
        )
        old_access = await client.get("/api/v1/me", headers=headers)
        new_access = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
        projects = await client.get("/api/v1/g", headers=new_access)

    assert started.status_code == 200
    assert callback.status_code == 303
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["id"] == guest["user"]["id"]
    assert refreshed.json()["user"]["is_anonymous"] is False
    assert old_access.status_code == 401
    assert project["id"] in {item["id"] for item in projects.json()}


@pytest.mark.asyncio
async def test_google_link_provider_conflict_keeps_guest_cookie_and_tier(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    class Provider:
        async def verify(self, token):
            if token == "guest-token":
                return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)
            return None

        async def link_google_identity(self, _token, _callback, _challenge):
            from app.adapters.live.supabase_auth import IdentityAlreadyLinked
            raise IdentityAlreadyLinked

    app.state.identity_provider = Provider()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        client.cookies.set("research_refresh_token", "guest-refresh",
                           domain="test.local", path="/api/v1/auth")
        start = await client.post("/api/v1/auth/upgrade/google/start",
                                  headers={"Authorization": "Bearer guest-token"})

    assert start.status_code == 409
    assert start.json()["detail"]["code"] == "GOOGLE_IDENTITY_CONFLICT"
    assert client.cookies.get("research_refresh_token") == "guest-refresh"
    assert app.state.identity_policy.tier_for("guest-1") == "guest"


@pytest.mark.asyncio
async def test_google_link_callback_conflict_error_preserves_guest_session(tmp_path):
    app = create_app(_settings(tmp_path))

    class Provider:
        callback = ""

        async def verify(self, _token):
            return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)

        async def link_google_identity(self, _token, callback, _challenge):
            self.callback = callback
            return "https://accounts.google.com/oauth"

        async def exchange_pkce(self, _code, _verifier):
            raise AssertionError("OAuth error must not exchange a code")

    provider = Provider()
    app.state.identity_provider = provider
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", follow_redirects=False) as client:
        start = await client.post("/api/v1/auth/upgrade/google/start",
                                  headers={"Authorization": "Bearer guest-token"})
        state = parse_qs(urlparse(provider.callback).query)["state"][0]
        client.cookies.set("research_refresh_token", "guest-refresh",
                           domain="test.local", path="/api/v1/auth")
        callback = await client.get("/api/v1/auth/google/callback", params={
            "state": state, "error": "server_error",
            "error_code": "identity_already_exists",
        })
        replay = await client.get("/api/v1/auth/google/callback", params={
            "state": state, "error": "server_error",
            "error_code": "identity_already_exists",
        })

    assert start.status_code == 200
    assert callback.status_code == 303
    assert callback.headers["location"].endswith("?error=GOOGLE_IDENTITY_CONFLICT")
    assert client.cookies.get("research_refresh_token") == "guest-refresh"
    assert app.state.identity_policy.tier_for("guest-1") == "guest"
    assert replay.status_code == 400
