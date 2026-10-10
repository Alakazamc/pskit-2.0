"""The production enforcing auth service exposes a public 429 without a database."""

from types import SimpleNamespace

import httpx
import pytest
from app.api.auth import router
from app.domain.auth_abuse import AuthAbuseLimited
from app.services.auth_protection import AuthProtection
from app.services.client_ip import TrustedClientIpResolver
from app.services.observability import RequestMetrics
from fastapi import FastAPI


class LimitedGuard:
    """Only the external PostgreSQL admission result is replaced."""

    def claim_login(self, _email, _client_ip):
        raise AuthAbuseLimited(17, "login_email")

    def consume_send_ip(self, _action, _client_ip):
        raise AuthAbuseLimited(17, "send_ip_10m")


class UncalledIdentityProvider:
    async def sign_in_password(self, *_args, **_kwargs):
        pytest.fail("Limited requests must stop before contacting Supabase")

    async def recover_password(self, *_args, **_kwargs):
        pytest.fail("Limited requests must stop before sending email")


@pytest.mark.asyncio
@pytest.mark.parametrize("route,payload", [
    ("/api/v1/auth/login", {"email": "user@example.org", "password": "secret"}),
    ("/api/v1/auth/password/recover", {"email": "user@example.org"}),
])
async def test_enforce_limits_are_429_with_retry_after_before_any_provider_request(route, payload):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(mode="live")
    app.state.identity_provider = UncalledIdentityProvider()
    app.state.auth_protection = AuthProtection(
        guard=LimitedGuard(), captcha=None,
        resolver=TrustedClientIpResolver(("127.0.0.1/32",)),
        metrics=RequestMetrics(), mode="enforce", captcha_required=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(route, json=payload)

    assert response.status_code == 429
    assert response.json() == {"detail": {"code": "AUTH_RATE_LIMITED"}}
    assert response.headers["Retry-After"] == "17"
