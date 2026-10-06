import asyncio

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


@pytest.mark.asyncio
async def test_mock_guest_is_created_only_after_request_and_reuses_refresh_cookie():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        before = await client.get("/api/v1/me")
        created = await client.post("/api/v1/auth/anonymous", json={})
        repeated = await client.post(
            "/api/v1/auth/anonymous", json={}, headers=await _csrf_headers(client)
        )
        me = await client.get("/api/v1/me", headers={
            "Authorization": f"Bearer {created.json().get('access_token', '')}",
        }) if created.status_code == 200 else None

    assert before.status_code == 401
    assert created.status_code == 200
    assert created.json()["user"]["is_anonymous"] is True
    assert "research_refresh_token=" in created.headers["set-cookie"]
    assert repeated.status_code == 200
    assert repeated.json()["user"]["id"] == created.json()["user"]["id"]
    assert me is not None and me.status_code == 200


@pytest.mark.asyncio
async def test_member_bearer_cannot_be_replaced_by_guest_login():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        member = await client.post("/api/v1/auth/demo", json={"email": "member@example.org"})
        anonymous = await client.post("/api/v1/auth/anonymous", json={}, headers={
            "Authorization": f"Bearer {member.json()['access_token']}",
            **await _csrf_headers(client),
        })

    assert member.status_code == 200
    assert anonymous.status_code == 409
    assert anonymous.json()["detail"]["code"] == "MEMBER_SESSION_ACTIVE"
    assert "research_refresh_token" not in anonymous.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_mock_guest_refresh_keeps_same_identity():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        guest = await client.post("/api/v1/auth/anonymous", json={})
        refreshed = await client.post(
            "/api/v1/auth/refresh", headers=await _csrf_headers(client)
        )

    assert guest.status_code == refreshed.status_code == 200
    assert refreshed.json()["user"]["id"] == guest.json()["user"]["id"]
    assert refreshed.json()["user"]["is_anonymous"] is True


@pytest.mark.asyncio
async def test_live_anonymous_signup_uses_python_supabase_boundary_and_records_guest(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret",
    )
    app = create_app(settings)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/signup":
            assert request.method == "POST"
            assert request.headers["apikey"] == "publishable-test"
            assert request.read() == b'{"data":{},"gotrue_meta_security":{"captcha_token":"proof"}}'
            return httpx.Response(200, json={
                "access_token": "guest-access", "refresh_token": "guest-refresh",
                "expires_in": 3600, "user": {"id": "guest-live", "is_anonymous": True},
            })
        assert request.url.path == "/auth/v1/user"
        assert request.headers["authorization"] == "Bearer guest-access"
        return httpx.Response(200, json={
            "id": "guest-live", "email": None, "is_anonymous": True, "user_metadata": {},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.post("/api/v1/auth/anonymous", json={"captcha_token": "proof"})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == "guest-live"
    assert response.json()["user"]["is_anonymous"] is True
    assert "HttpOnly" in response.headers["set-cookie"]
    assert app.state.identity_policy.tier_for("guest-live") == "guest"


@pytest.mark.asyncio
async def test_live_guest_cookie_restores_same_user_without_another_signup(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret",
    )
    app = create_app(settings)
    signups = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal signups
        if request.url.path == "/auth/v1/signup":
            signups += 1
            return httpx.Response(200, json={
                "access_token": "guest-access", "refresh_token": "guest-refresh",
                "expires_in": 3600,
            })
        if request.url.path == "/auth/v1/token":
            assert request.url.params["grant_type"] == "refresh_token"
            return httpx.Response(200, json={
                "access_token": "guest-access-next", "refresh_token": "guest-refresh-next",
                "expires_in": 3600,
            })
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={
            "id": "same-guest", "email": None, "is_anonymous": True, "user_metadata": {},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            first = await client.post("/api/v1/auth/anonymous", json={"captcha_token": "proof"})
            second = await client.post(
                "/api/v1/auth/anonymous", json={}, headers=await _csrf_headers(client)
            )

    assert first.status_code == second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"] == "same-guest"
    assert signups == 1


@pytest.mark.asyncio
async def test_live_member_bearer_does_not_create_guest_or_replace_session(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret",
    )
    app = create_app(settings)
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={
            "id": "member-live", "email": "member@example.org", "is_anonymous": False,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.post("/api/v1/auth/anonymous", json={}, headers={
                "Authorization": "Bearer member-access",
            })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MEMBER_SESSION_ACTIVE"
    assert calls == ["/auth/v1/user"]
    assert "research_refresh_token" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_two_live_instances_share_guest_creation_limit_and_ignore_forwarded_header(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret", anonymous_rate_limit_per_hour=1,
    )
    apps = [create_app(settings), create_app(settings)]

    class AnonymousProvider:
        async def sign_in_anonymously(self, captcha_token: str | None) -> SupabaseSession:
            return SupabaseSession("guest-access", "guest-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(id="guest-live", email="", name="Guest", is_anonymous=True)

    async def create(index: int) -> httpx.Response:
        apps[index].state.identity_provider = AnonymousProvider()
        transport = httpx.ASGITransport(app=apps[index], client=("198.51.100.9", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/v1/auth/anonymous", json={"captcha_token": "proof"},
                                     headers={"X-Forwarded-For": f"203.0.113.{index + 1}"})

    responses = await asyncio.gather(create(0), create(1))

    assert sorted(response.status_code for response in responses) == [200, 429]
    denied = next(response for response in responses if response.status_code == 429)
    assert denied.json()["detail"]["code"] == "ANONYMOUS_RATE_LIMITED"
    assert int(denied.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_member_bearer_takes_precedence_over_stale_guest_cookie(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret",
    )
    app = create_app(settings)

    class TwoIdentities:
        async def refresh(self, refresh_token: str) -> SupabaseSession:
            return SupabaseSession("old-guest-access", "old-guest-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(
                id="member-live" if access_token == "member-access" else "old-guest",
                email="member@example.org" if access_token == "member-access" else "",
                name="Member" if access_token == "member-access" else "Guest",
                is_anonymous=access_token != "member-access",
            )

    app.state.identity_provider = TwoIdentities()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        client.cookies.set("research_refresh_token", "old-guest-refresh", path="/api/v1/auth")
        response = await client.post("/api/v1/auth/anonymous", json={}, headers={
            "Authorization": "Bearer member-access", **await _csrf_headers(client),
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MEMBER_SESSION_ACTIVE"
    assert "research_refresh_token" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_malformed_supabase_identity_does_not_issue_guest_cookie(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        anonymous_enabled=True, anonymous_captcha_required=True,
        anonymous_rate_secret="shared-test-secret",
    )
    app = create_app(settings)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/signup":
            return httpx.Response(200, json={
                "access_token": "guest-access", "refresh_token": "guest-refresh", "expires_in": 3600,
            })
        return httpx.Response(200, text="not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.post("/api/v1/auth/anonymous", json={"captcha_token": "proof"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "IDENTITY_UNAVAILABLE"
    assert "research_refresh_token" not in response.headers.get("set-cookie", "")
