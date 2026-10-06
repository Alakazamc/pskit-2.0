"""CSRF boundary tests for refresh-cookie authenticated routes."""

import httpx
import pytest

import app.services.csrf as csrf_module
from app.config import Settings
from app.main import create_app
from app.services.csrf import CsrfProtector, CsrfRejected

TRUSTED_ORIGIN = "http://localhost:5174"


def test_csrf_token_is_bound_to_refresh_token_and_expires(monkeypatch):
    now = 1_000
    monkeypatch.setattr(csrf_module, "time", lambda: now)
    protector = CsrfProtector("c" * 32, TRUSTED_ORIGIN, lifetime_seconds=10)
    token = protector.issue("refresh-one")

    protector.verify("refresh-one", token, TRUSTED_ORIGIN)
    with pytest.raises(CsrfRejected):
        protector.verify("refresh-two", token, TRUSTED_ORIGIN)

    now = 1_011
    with pytest.raises(CsrfRejected):
        protector.verify("refresh-one", token, TRUSTED_ORIGIN)


async def _guest_with_csrf(client: httpx.AsyncClient) -> tuple[dict, str]:
    guest = await client.post("/api/v1/auth/anonymous", json={})
    token = await client.get("/api/v1/auth/csrf")
    assert guest.status_code == token.status_code == 200
    return guest.json(), token.json()["csrf_token"]


@pytest.mark.asyncio
async def test_refresh_cookie_requires_matching_origin_and_csrf_token():
    app = create_app(Settings(auth_csrf_secret="c" * 32))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        guest, token = await _guest_with_csrf(client)
        missing = await client.post("/api/v1/auth/refresh")
        foreign = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "https://attacker.example", "X-CSRF-Token": token},
        )
        invalid = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": f"{token}x"},
        )
        refreshed = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": token},
        )

    for rejected in (missing, foreign, invalid):
        assert rejected.status_code == 403
        assert rejected.json()["detail"]["code"] == "CSRF_FAILED"
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["id"] == guest["user"]["id"]


@pytest.mark.asyncio
async def test_anonymous_resume_and_logout_require_csrf_but_first_visit_does_not():
    app = create_app(Settings(auth_csrf_secret="c" * 32))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        guest, token = await _guest_with_csrf(client)
        resumed_without_token = await client.post("/api/v1/auth/anonymous", json={})
        resumed = await client.post(
            "/api/v1/auth/anonymous",
            json={},
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": token},
        )
        logout_without_token = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {guest['access_token']}"},
        )
        logged_out = await client.post(
            "/api/v1/auth/logout",
            headers={
                "Authorization": f"Bearer {guest['access_token']}",
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": token,
            },
        )

    assert resumed_without_token.status_code == 403
    assert resumed.status_code == 200
    assert logout_without_token.status_code == 403
    assert logged_out.status_code == 204


@pytest.mark.asyncio
async def test_csrf_bootstrap_has_no_token_without_refresh_cookie_and_is_not_cached():
    app = create_app(Settings(auth_csrf_secret="c" * 32))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/auth/csrf")

    assert response.status_code == 204
    assert response.headers["Cache-Control"] == "no-store"
