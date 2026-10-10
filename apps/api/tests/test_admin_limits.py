import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_admin_can_set_persistent_user_quotas_without_exposing_admin_key(tmp_path):
    settings = Settings(
        agent_runtime="pi",
        agent_db_path=str(tmp_path / "agent.sqlite3"),
        admin_api_key="private-admin-key",
    )
    app = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        user = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        headers = {"Authorization": f"Bearer {user['access_token']}"}
        path = f"/api/v1/admin/users/{user['user']['id']}/limits"
        missing = await client.put(path, json={"token_monthly_limit": 100, "gpu_daily_minutes": 10})
        wrong = await client.put(
            path,
            headers={"X-Admin-Key": "wrong"},
            json={
                "token_monthly_limit": 100,
                "gpu_daily_minutes": 10,
            },
        )
        updated = await client.put(
            path,
            headers={"X-Admin-Key": "private-admin-key"},
            json={
                "token_monthly_limit": 100,
                "gpu_daily_minutes": 10,
            },
        )
        usage = (await client.get("/api/v1/usage", headers=headers)).json()
        rejected = await client.post(
            "/api/v1/af3/jobs",
            headers=headers,
            json={"estimated_gpu_minutes": 20},
        )

    restarted = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        token = (
            await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        ).json()["access_token"]
        saved = (
            await client.get(
                "/api/v1/usage",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()

    assert missing.status_code in {401, 403}
    assert wrong.status_code == 403
    assert updated.status_code == 200
    assert "private-admin-key" not in updated.text
    assert usage["tokens"]["limit"] == saved["tokens"]["limit"] == 100
    assert usage["gpu"]["limit"] == saved["gpu"]["limit"] == 10
    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_admin_limit_route_is_unavailable_without_operator_configuration():
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/v1/admin/users/alice/limits",
            headers={"X-Admin-Key": "guessed"},
            json={"token_monthly_limit": 100, "gpu_daily_minutes": 10},
        )
    assert response.status_code == 404
