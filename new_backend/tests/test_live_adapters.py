import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.live.supabase_auth import AuthRateLimited, SupabaseIdentityAdapter
from app.config import Settings
from app.main import create_app
from app.ports.providers import IdentityTransportUnavailable

pytestmark = pytest.mark.usefixtures("live_database")


async def _csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"Origin": "http://localhost:5174", "X-CSRF-Token": response.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_supabase_auth_calls_receive_only_the_server_client_ip_header():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
            })
        if request.url.path == "/auth/v1/verify":
            return httpx.Response(200, json={
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
            })
        if request.url.path == "/auth/v1/signup" and json.loads(request.content).get("email") is None:
            return httpx.Response(200, json={
                "access_token": "guest", "refresh_token": "refresh", "expires_in": 3600,
            })
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client,
        )
        await adapter.sign_in_password("a@example.org", "secret", client_ip="198.51.100.7")
        await adapter.sign_up("a@example.org", "secret", client_ip="198.51.100.7")
        await adapter.recover_password("a@example.org", client_ip="198.51.100.7")
        await adapter.verify_otp("a@example.org", "123456", "signup", client_ip="198.51.100.7")
        await adapter.update_guest_email("guest", "a@example.org", client_ip="198.51.100.7")
        await adapter.sign_in_anonymously("captcha", client_ip="198.51.100.7")

    assert len(requests) == 6
    assert {request.headers["X-PSKit-Client-IP"] for request in requests} == {"198.51.100.7"}


class FailingTransport(httpx.AsyncBaseTransport):
    def __init__(self, error_type):
        self.error_type = error_type
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise self.error_type("provider failed", request=request)


@pytest.mark.parametrize(
    ("error_type", "delivery"),
    [
        (httpx.ConnectError, "not_sent"),
        (httpx.ConnectTimeout, "not_sent"),
        (httpx.PoolTimeout, "not_sent"),
        (httpx.WriteTimeout, "unknown"),
        (httpx.ReadTimeout, "unknown"),
        (httpx.RemoteProtocolError, "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_supabase_transport_failure_classifies_delivery_without_retry(error_type, delivery):
    transport = FailingTransport(error_type)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client,
        )
        with pytest.raises(IdentityTransportUnavailable) as caught:
            await adapter.sign_up(
                "a@example.org", "secret", client_ip="198.51.100.7",
            )

    assert caught.value.delivery == delivery
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_supabase_explicit_rate_limit_is_not_transport_failure_or_retried():
    calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "17"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client,
        )
        with pytest.raises(AuthRateLimited) as caught:
            await adapter.sign_up(
                "a@example.org", "secret", client_ip="198.51.100.7",
            )

    assert caught.value.retry_after == "17"
    assert calls == 1


@pytest.mark.asyncio
async def test_google_start_uses_public_supabase_url():
    settings = Settings(
        mode="live", supabase_url="http://api-gw:8000",
        supabase_public_url="https://agent.bioailab.net",
        supabase_publishable_key="publishable-test",
        public_api_url="https://agent.bioailab.net",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", follow_redirects=False) as client:
        response = await client.get("/api/v1/auth/google/start")

    redirect = urlparse(response.headers["location"])
    assert redirect.scheme == "https"
    assert redirect.netloc == "agent.bioailab.net"
    assert redirect.path == "/auth/v1/authorize"
    assert parse_qs(redirect.query)["redirect_to"][0].startswith(
        "https://agent.bioailab.net/api/v1/auth/google/callback?"
    )
    assert app.state.settings.supabase_url == "http://api-gw:8000"


@pytest.mark.asyncio
async def test_google_start_falls_back_to_internal_url():
    app = create_app(Settings(
        mode="live", supabase_url="http://api-gw:8000",
        supabase_publishable_key="publishable-test",
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", follow_redirects=False) as client:
        response = await client.get("/api/v1/auth/google/start")

    assert urlparse(response.headers["location"]).netloc == "api-gw:8000"


@pytest.mark.asyncio
async def test_anonymous_signup_sends_supabase_captcha_without_email_or_password():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/auth/v1/signup"
        assert request.headers["apikey"] == "publishable-test"
        assert json.loads(request.content) == {
            "data": {},
            "gotrue_meta_security": {"captcha_token": "captcha-proof"},
        }
        return httpx.Response(200, json={
            "access_token": "guest-access", "refresh_token": "guest-refresh", "expires_in": 3600,
            "user": {"id": "guest-1", "is_anonymous": True},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = await SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client,
        ).sign_in_anonymously("captcha-proof")

    assert session.access_token == "guest-access"
    assert session.refresh_token == "guest-refresh"


@pytest.mark.parametrize("route", ["login", "refresh"])
@pytest.mark.asyncio
async def test_supabase_token_rate_limit_is_forwarded_without_retry(route: str):
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/auth/v1/token"
        return httpx.Response(429, headers={"Retry-After": "3"})

    settings = Settings(mode="live", supabase_url="https://example.supabase.co",
                        supabase_publishable_key="publishable-test")
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if route == "refresh":
                client.cookies.set("research_refresh_token", "old", path="/api/v1/auth")
                response = await client.post(
                    "/api/v1/auth/refresh", headers=await _csrf_headers(client)
                )
            else:
                response = await client.post("/api/v1/auth/login", json={
                    "email": "alice@example.org", "password": "secret",
                })

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "AUTH_RATE_LIMITED"
    assert response.headers["Retry-After"] == "3"
    assert calls == 1


@pytest.mark.asyncio
async def test_google_exchange_rate_limit_is_forwarded():
    settings = Settings(mode="live", supabase_url="https://example.supabase.co",
                        supabase_publishable_key="publishable-test")
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "7"}))
    ) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test", follow_redirects=False) as client:
            started = await client.get("/api/v1/auth/google/start")
            redirect_to = parse_qs(urlparse(started.headers["location"]).query)["redirect_to"][0]
            state = parse_qs(urlparse(redirect_to).query)["state"][0]
            response = await client.get("/api/v1/auth/google/callback", params={
                "state": state, "code": "auth-code",
            })

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


@pytest.mark.asyncio
async def test_supabase_adapter_verifies_token_with_auth_server():
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.supabase.co/auth/v1/user"
        assert request.headers["apikey"] == "publishable-test"
        assert request.headers["authorization"] == "Bearer signed-user-token"
        return httpx.Response(
            200,
            json={
                "id": "user-123",
                "email": "alice@example.org",
                "user_metadata": {"full_name": "Alice"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        identity = await SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client
        ).verify("signed-user-token")

    assert identity is not None
    assert identity.id == "user-123"
    assert identity.name == "Alice"


@pytest.mark.asyncio
async def test_supabase_verified_anonymous_user_stays_anonymous():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={
            "id": "guest-1", "email": None, "is_anonymous": True, "user_metadata": {},
        }),
    )) as client:
        identity = await SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client,
        ).verify("guest-access")

    assert identity is not None
    assert identity.id == "guest-1"
    assert identity.is_anonymous is True


@pytest.mark.asyncio
async def test_supabase_adapter_rejects_expired_token():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401))
    ) as client:
        identity = await SupabaseIdentityAdapter(
            "https://example.supabase.co", "publishable-test", client
        ).verify("expired")

    assert identity is None


@pytest.mark.asyncio
async def test_python_email_login_exchanges_supabase_password_for_jwt_and_refresh_cookie():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            assert request.url.params["grant_type"] == "password"
            assert request.headers["apikey"] == "publishable-test"
            assert request.content == b'{"email":"alice@example.org","password":"secret"}'
            return httpx.Response(200, json={
                "access_token": "signed-user-token", "refresh_token": "private-refresh-token",
                "expires_in": 3600,
            })
        assert request.url.path == "/auth/v1/user"
        assert request.headers["authorization"] == "Bearer signed-user-token"
        return httpx.Response(200, json={"id": "user-123", "email": "alice@example.org"})

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/v1/auth/login", json={"email": "alice@example.org", "password": "secret"})
            me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})

    assert login.status_code == 200
    assert login.json()["user"]["id"] == "user-123"
    assert "refresh_token" not in login.json()
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "private-refresh-token" in login.headers["set-cookie"]
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_python_refresh_rotates_supabase_token_without_exposing_refresh_secret():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            assert request.url.params["grant_type"] == "refresh_token"
            assert request.content == b'{"refresh_token":"old-refresh"}'
            return httpx.Response(200, json={
                "access_token": "fresh-jwt", "refresh_token": "new-refresh", "expires_in": 3600,
            })
        assert request.headers["authorization"] == "Bearer fresh-jwt"
        return httpx.Response(200, json={"id": "user-123", "email": "alice@example.org"})

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post("/api/v1/auth/refresh")
            client.cookies.set("research_refresh_token", "old-refresh", path="/api/v1/auth")
            refreshed = await client.post(
                "/api/v1/auth/refresh", headers=await _csrf_headers(client)
            )

    assert missing.status_code == 401
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] == "fresh-jwt"
    assert "refresh_token" not in refreshed.json()
    assert "new-refresh" in refreshed.headers["set-cookie"]


@pytest.mark.asyncio
async def test_invalid_refresh_clears_browser_cookie():
    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401))
    ) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.cookies.set("research_refresh_token", "expired", path="/api/v1/auth")
            response = await client.post(
                "/api/v1/auth/refresh", headers=await _csrf_headers(client)
            )
    assert response.status_code == 401
    assert 'research_refresh_token=""' in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_refresh_clears_cookie_when_supabase_cannot_verify_new_access_token():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={
                "access_token": "unverifiable", "refresh_token": "rotated", "expires_in": 3600,
            })
        return httpx.Response(401)

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.cookies.set("research_refresh_token", "old", path="/api/v1/auth")
            response = await client.post(
                "/api/v1/auth/refresh", headers=await _csrf_headers(client)
            )
    assert response.status_code == 401
    assert 'research_refresh_token=""' in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_google_oauth_starts_and_finishes_in_python_with_pkce():
    challenge: str | None = None

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            assert request.url.params["grant_type"] == "pkce"
            body = json.loads(request.content)
            assert body["auth_code"] == "returned-code"
            verifier_hash = hashlib.sha256(body["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(verifier_hash).decode().rstrip("=") == challenge
            return httpx.Response(200, json={
                "access_token": "google-jwt", "refresh_token": "google-refresh", "expires_in": 3600,
            })
        assert request.headers["authorization"] == "Bearer google-jwt"
        return httpx.Response(200, json={
            "id": "google-user", "email": "alice@example.org", "is_anonymous": False,
        })

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
        frontend_url="http://localhost:5174", public_api_url="http://localhost:18080",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:18080", follow_redirects=False) as client:
            started = await client.get("/api/v1/auth/google/start")
            target = urlparse(started.headers["location"])
            params = parse_qs(target.query)
            challenge = params["code_challenge"][0]
            redirect_to = urlparse(params["redirect_to"][0])
            state = parse_qs(redirect_to.query)["state"][0]
            invalid = await client.get("/api/v1/auth/google/callback", params={"state": "wrong", "code": "returned-code"})
            finished = await client.get("/api/v1/auth/google/callback", params={"state": state, "code": "returned-code"})

    assert target.path == "/auth/v1/authorize"
    assert params["provider"] == ["google"]
    assert params["code_challenge_method"] == ["s256"]
    assert redirect_to.path == "/api/v1/auth/google/callback"
    assert invalid.status_code == 400
    assert finished.status_code == 303
    assert finished.headers["location"] == "http://localhost:5174/auth/callback"
    assert "google-refresh" in finished.headers["set-cookie"]
    assert "google-jwt" not in finished.headers["location"]


@pytest.mark.asyncio
async def test_google_oauth_callback_can_finish_in_another_python_instance(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "auth.sqlite3"),
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test",
    )
    first = create_app(settings)
    second = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        started = await client.get("/api/v1/auth/google/start")
        redirect_to = parse_qs(urlparse(started.headers["location"]).query)["redirect_to"][0]
        state = parse_qs(urlparse(redirect_to).query)["state"][0]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            assert request.url.params["grant_type"] == "pkce"
            return httpx.Response(200, json={
                "access_token": "google-jwt", "refresh_token": "google-refresh",
                "expires_in": 3600,
            })
        return httpx.Response(200, json={
            "id": "google-user", "email": "alice@example.org", "is_anonymous": False,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        second.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://test") as client:
            client.cookies.set("research_oauth_state", state, path="/api/v1/auth/google/callback")
            finished = await client.get("/api/v1/auth/google/callback", params={
                "state": state, "code": "returned-code",
            })
            replay = await client.get("/api/v1/auth/google/callback", params={
                "state": state, "code": "returned-code",
            })

    assert finished.status_code == 303
    assert replay.status_code == 400
    assert first.state.identity_policy.tier_for("google-user") == "member"


@pytest.mark.asyncio
async def test_python_logout_revokes_supabase_session_and_clears_refresh_cookie():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/logout"
        assert request.headers["authorization"] == "Bearer signed-jwt"
        return httpx.Response(204)

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.cookies.set("research_refresh_token", "old-refresh", path="/api/v1/auth")
            logged_out = await client.post("/api/v1/auth/logout", headers={
                "Authorization": "Bearer signed-jwt", **await _csrf_headers(client),
            })

    assert logged_out.status_code == 204
    assert "research_refresh_token=" in logged_out.headers["set-cookie"]
    assert "old-refresh" not in logged_out.headers["set-cookie"]


@pytest.mark.asyncio
async def test_live_usage_returns_token_allowance_without_new_api_quota_points():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={"id": "user-123", "email": "alice@example.org"})

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test", new_api_base_url="https://new-api.example",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            usage = await client.get("/api/v1/usage", headers={"Authorization": "Bearer signed-jwt"})

    assert usage.status_code == 200
    assert set(usage.json()) == {"tokens", "gpu", "storage"}
    assert usage.json()["tokens"]["unit"] == "tokens"


def test_live_app_rejects_missing_provider_configuration():
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        create_app(Settings(mode="live"))


@pytest.mark.asyncio
async def test_supabase_login_runs_without_a_model_gateway():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={
                "access_token": "signed-jwt", "refresh_token": "private-refresh", "expires_in": 3600,
            })
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})
        raise AssertionError(f"Unexpected Supabase request: {request.url}")

    settings = Settings(
        mode="live", supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/v1/auth/login", json={
                "email": "alice@example.org", "password": "password",
            })
            me = await client.get("/api/v1/me", headers={"Authorization": "Bearer signed-jwt"})

    assert login.status_code == 200
    assert login.json()["user"]["id"] == "alice"
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_live_pi_can_use_a_shared_openai_compatible_gateway(tmp_path):
    class CapturingPi:
        def __init__(self):
            self.environments = []

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.environments.append(kwargs["environment"])
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})

    settings = Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example/v1",
        model_gateway_model="research-model", model_gateway_api_key="server-secret",
    )
    runner = CapturingPi()
    app = create_app(settings, pi_runner=runner)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer signed-jwt"}
            (await client.get("/api/v1/g", headers=headers)).json()[0]
            session = (await client.post("/api/v1/c", headers=headers,
                                         json={"title": "Gateway test"})).json()
            sent = await client.post(f"/api/v1/c/{session['id']}/messages", headers=headers,
                                     json={"content": "hello"})
            await asyncio.sleep(0.02)

    assert sent.status_code == 200
    assert len(runner.environments) == 1
    proxy_key = runner.environments[0]["MODEL_GATEWAY_API_KEY"]
    proxy_run_id, proxy_token = proxy_key.split(".", 1)
    assert app.state.agent_service.verify_tool_token(proxy_run_id, proxy_token)
    assert "server-secret" not in str(runner.environments[0])
    assert "server-secret" not in sent.text


@pytest.mark.asyncio
async def test_generic_gateway_without_shared_key_rejects_message(tmp_path):
    settings = Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        model_gateway_base_url="https://gateway.example/v1",
        model_gateway_model="research-model", model_gateway_api_key="",
    )
    app = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})
    )) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer signed-jwt"}
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "hello"},
            )

    assert sent.status_code == 503
    assert sent.json()["detail"]["code"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_python_supabase_signup_and_password_recovery_are_versioned_api_flows(tmp_path):
    calls: list[tuple[str, dict]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, payload))
        if request.url.path == "/auth/v1/signup":
            return httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})
        if request.url.path == "/auth/v1/recover":
            return httpx.Response(200, json={})
        if request.url.path == "/auth/v1/verify":
            return httpx.Response(200, json={
                "access_token": "verified-jwt", "refresh_token": "verified-refresh",
                "expires_in": 3600,
            })
        if request.url.path == "/auth/v1/user" and request.method == "GET":
            return httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})
        if request.url.path == "/auth/v1/user" and request.method == "PUT":
            assert request.headers["authorization"] == "Bearer verified-jwt"
            return httpx.Response(200, json={"id": "alice", "email": "alice@example.org"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            signup = await client.post("/api/v1/auth/signup", json={
                "email": "alice@example.org", "password": "long-password",
            })
            recovery = await client.post("/api/v1/auth/password/recover", json={
                "email": "alice@example.org",
            })
            verified = await client.post("/api/v1/auth/verify", json={
                "email": "alice@example.org", "token": "123456", "type": "recovery",
            })
            updated = await client.post(
                "/api/v1/auth/password/update",
                headers={"Authorization": "Bearer verified-jwt"},
                json={"password": "new-long-password"},
            )

    assert signup.status_code == 200 and signup.json()["status"] == "check_email"
    assert recovery.status_code == 200 and recovery.json()["status"] == "email_sent"
    assert verified.status_code == 200 and verified.json()["access_token"] == "verified-jwt"
    assert "verified-refresh" in verified.headers["set-cookie"]
    assert updated.status_code == 204
    assert calls[0] == ("/auth/v1/signup", {"email": "alice@example.org", "password": "long-password"})
    assert calls[1] == ("/auth/v1/recover", {"email": "alice@example.org"})
    assert calls[2] == ("/auth/v1/verify", {
        "email": "alice@example.org", "token": "123456", "type": "recovery",
    })
    assert calls[-1] == ("/auth/v1/user", {"password": "new-long-password"})


@pytest.mark.asyncio
async def test_live_pi_without_shared_gateway_key_rejects_all_users(tmp_path):
    class CapturingPi:
        def __init__(self):
            self.environments = []

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.environments.append(kwargs["environment"])
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        user_id = request.headers["authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json={"id": user_id, "email": f"{user_id}@example.org"})

    settings = Settings(
        mode="live", agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        new_api_base_url="https://new-api.example", new_api_model="research-model",
        user_token_limits_json='{"alice":12}',
    )
    runner = CapturingPi()
    app = create_app(settings, pi_runner=runner)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as supabase:
        app.state.identity_provider = SupabaseIdentityAdapter(
            settings.supabase_url, settings.supabase_publishable_key, supabase
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = {"Authorization": "Bearer alice"}
            bob = {"Authorization": "Bearer bob"}
            project_a = (await client.get("/api/v1/g", headers=alice)).json()[0]
            project_b = (await client.get("/api/v1/g", headers=bob)).json()[0]
            usage = (await client.get("/api/v1/usage", headers=alice)).json()
            denied = await client.post(
                f"/api/v1/c/session-{project_b['id'].removeprefix('project-')}/messages",
                headers=bob, json={"content": "hello"},
            )
            sent = await client.post(
                f"/api/v1/c/session-{project_a['id'].removeprefix('project-')}/messages",
                headers=alice, json={"content": "hello"},
            )
            await asyncio.sleep(0.02)
    assert usage["tokens"]["limit"] == 12
    assert denied.status_code == 503
    assert denied.json()["detail"]["code"] == "MODEL_NOT_CONFIGURED"
    assert sent.status_code == 503
    assert sent.json()["detail"]["code"] == "MODEL_NOT_CONFIGURED"
    assert runner.environments == []


def test_live_configuration_rejects_legacy_user_key_mapping():
    settings = Settings(
        mode="live", agent_runtime="pi",
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
        new_api_base_url="https://new-api.example", new_api_model="research-model",
        new_api_user_tokens_json='{"alice":"secret-alice"}',
    )
    with pytest.raises(ValueError, match="NEW_API_USER_TOKENS_JSON is no longer supported"):
        create_app(settings, pi_runner=object())
