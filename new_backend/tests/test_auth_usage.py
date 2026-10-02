import httpx
import pytest

from app.adapters.mock.auth import MockIdentityProvider
from app.domain.store import DemoStore
from app.main import create_app


@pytest.mark.asyncio
async def test_mock_guest_token_is_separate_from_demo_member_identity():
    store = DemoStore()
    member_token, member = store.issue_token("alice@example.org")
    guest_token, guest = store.issue_anonymous_token()
    provider = MockIdentityProvider(store)

    assert guest_token != member_token
    assert guest.id != member.id
    assert guest.is_anonymous is True
    assert (await provider.verify(guest_token)).id == guest.id
    assert (await provider.verify(member_token)).is_anonymous is False


@pytest.mark.asyncio
async def test_demo_login_returns_user_and_distinct_usage_units():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert login.json()["user"]["email"] == "alice@example.org"
        usage = await client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"})
    assert usage.status_code == 200
    body = usage.json()
    assert body["tokens"]["unit"] == "tokens"
    assert set(body) == {"tokens", "gpu", "storage"}
    assert body["storage"]["unit"] == "bytes"
    assert body["gpu"]["unit"] == "gpu_minutes"
    assert body["gpu"]["period"] == "day"
    assert body["gpu"]["resets_at"]


@pytest.mark.asyncio
async def test_protected_usage_requires_auth():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/usage")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_demo_users_have_separate_identities():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()

        alice_me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {alice['access_token']}"}
        )
        bob_me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {bob['access_token']}"}
        )

    assert alice_me.status_code == bob_me.status_code == 200
    assert alice_me.json()["id"] != bob_me.json()["id"]
    assert alice_me.json()["email"] == "alice@example.org"
    assert bob_me.json()["email"] == "bob@example.org"


@pytest.mark.asyncio
async def test_token_allowance_is_per_user_and_blocks_exhausted_user():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        app.state.quotas.set_token_limit(alice["user"]["id"], 0)
        (await client.get("/api/v1/g", headers=alice_headers)).json()[0]
        session = (
            await client.get("/api/v1/c", headers=alice_headers)
        ).json()[0]
        denied = await client.post(
            f"/api/v1/c/{session['id']}/messages",
            headers=alice_headers,
            json={"content": "Hello"},
        )
        alice_usage = (await client.get("/api/v1/usage", headers=alice_headers)).json()["tokens"]
        bob_usage = (await client.get("/api/v1/usage", headers=bob_headers)).json()["tokens"]
        messages = (await client.get(f"/api/v1/c/{session['id']}/messages", headers=alice_headers)).json()

    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "TOKEN_QUOTA_EXCEEDED"
    assert alice_usage["remaining"] == 0
    assert alice_usage["reserved"] == 0
    assert bob_usage["remaining"] > 0
    assert messages == []
