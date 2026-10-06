import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_mock_guest_logout_revokes_bearer_and_refresh_cookie():
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        guest = (await client.post("/api/v1/auth/anonymous", json={})).json()
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        headers = {
            "Authorization": f"Bearer {guest['access_token']}",
            "Origin": "http://localhost:5174",
            "X-CSRF-Token": csrf,
        }
        signed_out = await client.post("/api/v1/auth/logout", headers=headers)
        restored = await client.post("/api/v1/auth/refresh")
        old_token = await client.get("/api/v1/me", headers=headers)

    assert signed_out.status_code == 204
    assert restored.status_code == 401
    assert old_token.status_code == 401
