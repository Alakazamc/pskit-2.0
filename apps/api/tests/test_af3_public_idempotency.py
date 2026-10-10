import httpx
import pytest

from app.config import Settings
from app.main import create_app


async def _login(client: httpx.AsyncClient, email: str):
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    assert response.status_code == 200
    return response.json()["user"]["id"], {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


@pytest.mark.asyncio
async def test_public_af3_idempotency_prevents_duplicate_gpu_reservations_across_instances(tmp_path):
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-secret",
        admin_api_key="admin-secret",
    )
    first = create_app(settings, pi_runner=object())
    second = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first), base_url="http://test",
    ) as one, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second), base_url="http://test",
    ) as two:
        alice_id, alice_one = await _login(one, "alice@example.org")
        _, alice_two = await _login(two, "alice@example.org")
        _, bob = await _login(two, "bob@example.org")
        limited = await one.put(
            f"/api/v1/admin/users/{alice_id}/limits",
            headers={"X-Admin-Key": "admin-secret"},
            json={"token_monthly_limit": 100_000, "gpu_daily_minutes": 15},
        )
        assert limited.status_code == 200
        payload = {"estimated_gpu_minutes": 15}
        original = await one.post(
            "/api/v1/af3/jobs", headers={**alice_one, "Idempotency-Key": "submit-one"},
            json=payload,
        )
        replay = await two.post(
            "/api/v1/af3/jobs", headers={**alice_two, "Idempotency-Key": "submit-one"},
            json=payload,
        )
        changed = await two.post(
            "/api/v1/af3/jobs", headers={**alice_two, "Idempotency-Key": "submit-one"},
            json={"estimated_gpu_minutes": 10},
        )
        bob_submit = await two.post(
            "/api/v1/af3/jobs", headers={**bob, "Idempotency-Key": "submit-one"},
            json=payload,
        )
        usage = await two.get("/api/v1/usage", headers=alice_two)
        cancelled = await two.delete(f"/api/v1/af3/jobs/{original.json()['id']}", headers=alice_two)
        replay_after_cancel = await one.post(
            "/api/v1/af3/jobs", headers={**alice_one, "Idempotency-Key": "submit-one"},
            json=payload,
        )

    restarted = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://test",
    ) as client:
        _, alice_restarted = await _login(client, "alice@example.org")
        replay_after_restart = await client.post(
            "/api/v1/af3/jobs", headers={**alice_restarted, "Idempotency-Key": "submit-one"},
            json=payload,
        )

    assert original.status_code == replay.status_code == 200
    assert original.json()["id"] == replay.json()["id"]
    assert original.json()["simulation"] is False
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "AF3_IDEMPOTENCY_CONFLICT"
    assert bob_submit.status_code == 200
    assert bob_submit.json()["id"] != original.json()["id"]
    assert usage.json()["gpu"]["reserved"] == 15
    assert cancelled.status_code == 200
    assert replay_after_cancel.status_code == 200
    assert replay_after_cancel.json()["id"] == original.json()["id"]
    assert replay_after_cancel.json()["status"] == "cancelled"
    assert replay_after_restart.status_code == 200
    assert replay_after_restart.json()["id"] == original.json()["id"]
    assert replay_after_restart.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_mock_af3_submission_reuses_key_and_rejects_overlong_key():
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        _, alice = await _login(client, "alice@example.org")
        headers = {**alice, "Idempotency-Key": "mock-submit"}
        first = await client.post("/api/v1/af3/jobs", headers=headers, json={})
        second = await client.post("/api/v1/af3/jobs", headers=headers, json={})
        too_long = await client.post(
            "/api/v1/af3/jobs", headers={**alice, "Idempotency-Key": "x" * 129}, json={},
        )
        usage = await client.get("/api/v1/usage", headers=alice)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["simulation"] is True
    assert too_long.status_code == 422
    assert usage.json()["gpu"]["reserved"] == 20
