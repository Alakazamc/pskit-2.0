import httpx
import pytest

from app.adapters.live.supabase_auth import InvalidCredentials
from app.config import Settings
from app.contracts.models import UserIdentity
from app.domain.auth_abuse import AuthAbuseLimited, AuthClaim
from app.main import create_app
from app.ports.providers import IdentityTransportUnavailable
from app.services.auth_protection import AuthAuthorization

pytestmark = pytest.mark.usefixtures("live_database")


class FakeProtection:
    def __init__(self, failure=None):
        self.failure = failure
        self.settlements = []

    async def authorize_email_send(self, action, email, captcha_token, **_network):
        if self.failure:
            raise self.failure
        return AuthAuthorization(AuthClaim("send", f"{action}_send"), "203.0.113.9")

    def authorize_login(self, email, **_network):
        if self.failure:
            raise self.failure
        return AuthAuthorization(AuthClaim("login", "password_login"), "203.0.113.9")

    def authorize_verification(self, action, email, **_network):
        if self.failure:
            raise self.failure
        return AuthAuthorization(AuthClaim("verify", action), "203.0.113.9")

    def settle(self, claim, outcome):
        self.settlements.append((claim.token if claim else None, outcome))


def live_app():
    return create_app(
        Settings(
            mode="live",
            supabase_url="https://example.supabase.co",
            supabase_publishable_key="publishable-test",
        )
    )


@pytest.mark.asyncio
async def test_guard_limit_is_public_429_with_integer_retry_after():
    app = live_app()
    app.state.auth_protection = FakeProtection(AuthAbuseLimited(17, "login_email"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "user@example.org",
                "password": "secret",
            },
        )

    assert response.status_code == 429
    assert response.json() == {"detail": {"code": "AUTH_RATE_LIMITED"}}
    assert response.headers["Retry-After"] == "17"


@pytest.mark.asyncio
async def test_recovery_hides_unknown_accounts_and_retains_attempt():
    app = live_app()
    protection = FakeProtection()

    class UnknownAccount:
        async def recover_password(self, email, *, client_ip=None):
            assert client_ip == "203.0.113.9"
            raise InvalidCredentials

    app.state.auth_protection = protection
    app.state.identity_provider = UnknownAccount()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/password/recover",
            json={
                "email": "missing@example.org",
                "captcha_token": "proof",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "email_sent"}
    assert protection.settlements == [("send", "rejected")]


@pytest.mark.asyncio
async def test_definitely_unsent_signup_refunds_email_claim():
    app = live_app()
    protection = FakeProtection()

    class OfflineBeforeSend:
        async def sign_up(self, email, password, *, client_ip=None):
            assert client_ip == "203.0.113.9"
            raise IdentityTransportUnavailable("not_sent")

    app.state.auth_protection = protection
    app.state.identity_provider = OfflineBeforeSend()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "new@example.org",
                "password": "12345678",
                "captcha_token": "proof",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "IDENTITY_UNAVAILABLE"}}
    assert protection.settlements == [("send", "not_sent")]


@pytest.mark.asyncio
async def test_successful_login_forwards_only_resolved_ip_and_releases_account_attempt():
    app = live_app()
    protection = FakeProtection()

    class SignedIn:
        async def sign_in_password(self, email, password, *, client_ip=None):
            assert client_ip == "203.0.113.9"
            from app.adapters.live.supabase_auth import SupabaseSession

            return SupabaseSession("access", "refresh", 3600)

        async def verify(self, token):
            return UserIdentity(
                id="user-1",
                email="user@example.org",
                name="User",
                is_anonymous=False,
            )

    app.state.auth_protection = protection
    app.state.identity_provider = SignedIn()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "user@example.org",
                "password": "secret",
            },
        )

    assert response.status_code == 200
    assert protection.settlements == [("login", "success")]
