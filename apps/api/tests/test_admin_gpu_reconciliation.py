import httpx
import pytest

from app.config import Settings
from app.main import create_app

FOLD_INPUT = {
    "name": "small protein",
    "modelSeeds": [1],
    "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
    "dialect": "alphafold3",
    "version": 4,
}
WORKER = {
    "worker_id": "a6000",
    "resources": {
        "capabilities": ["af3"],
        "gpu_count": 1,
        "gpu_memory_mb": 49_152,
    },
}


@pytest.mark.asyncio
async def test_admin_can_audit_and_correct_unreported_gpu_usage_without_resuming_job(tmp_path):
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=str(tmp_path / "agent.sqlite3"),
            admin_api_key="admin-secret",
            af3_executor="callback",
            compute_callback_key="compute-key",
        ),
        pi_runner=object(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {login['access_token']}"}
        created = (
            await client.post(
                "/api/v1/af3/jobs",
                headers=user,
                json={
                    "estimated_gpu_minutes": 20,
                    "fold_input": FOLD_INPUT,
                },
            )
        ).json()
        path = f"/api/v1/admin/af3/jobs/{created['id']}/gpu-usage"
        key = {"X-Admin-Key": "admin-secret"}
        active = await client.put(
            path,
            headers=key,
            json={
                "actual_gpu_minutes": 0,
                "reason": "worker says zero usage",
            },
        )
        claim = (
            await client.post(
                "/internal/compute/af3/jobs/claim",
                headers={"X-Compute-Key": "compute-key"},
                json=WORKER,
            )
        ).json()[0]
        cancelled = await client.delete(f"/api/v1/af3/jobs/{created['id']}", headers=user)
        missing = await client.put(
            path,
            json={
                "actual_gpu_minutes": 12,
                "reason": "operator verified worker logs",
            },
        )
        wrong = await client.put(
            path,
            headers={"X-Admin-Key": "wrong"},
            json={
                "actual_gpu_minutes": 12,
                "reason": "operator verified worker logs",
            },
        )
        first = await client.put(
            path,
            headers=key,
            json={
                "actual_gpu_minutes": 12,
                "reason": "operator verified worker logs",
            },
        )
        repeated = await client.put(
            path,
            headers=key,
            json={
                "actual_gpu_minutes": 12,
                "reason": "operator verified worker logs",
            },
        )
        corrected = await client.put(
            path,
            headers=key,
            json={
                "actual_gpu_minutes": 9,
                "reason": "final GPU telemetry correction",
            },
        )
        late_conflict = await client.post(
            f"/internal/af3/jobs/{created['id']}/result",
            headers={"X-Compute-Key": "compute-key"},
            json={
                "status": "failed",
                "actual_gpu_minutes": 12,
                "attempt": claim["attempt"],
                "lease_token": claim["lease_token"],
            },
        )
        audit = await client.get(f"{path}/audit", headers=key)
        usage = (await client.get("/api/v1/usage", headers=user)).json()["gpu"]

    assert active.status_code == 409
    assert cancelled.json()["gpu_accounting_status"] == "pending_reconciliation"
    assert missing.status_code == wrong.status_code == 403
    assert first.status_code == repeated.status_code == corrected.status_code == 200
    assert first.json()["status"] == corrected.json()["status"] == "cancelled"
    assert corrected.json()["gpu_accounting_status"] == "reconciled"
    assert corrected.json()["actual_gpu_minutes"] == 9
    assert late_conflict.status_code == 409
    assert late_conflict.json()["detail"]["code"] == "GPU_RECONCILIATION_CONFLICT"
    assert (usage["used"], usage["reserved"], usage["remaining"]) == (9, 0, 51)
    assert audit.status_code == 200
    assert [
        (item["source"], item["previous_minutes"], item["actual_minutes"]) for item in audit.json()
    ] == [("admin", None, 12), ("admin", 12, 9)]
    assert [item["reason"] for item in audit.json()] == [
        "operator verified worker logs",
        "final GPU telemetry correction",
    ]


@pytest.mark.asyncio
async def test_gpu_reconciliation_admin_route_is_hidden_without_admin_key_configuration():
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/v1/admin/af3/jobs/unknown/gpu-usage",
            headers={"X-Admin-Key": "guessed"},
            json={
                "actual_gpu_minutes": 0,
                "reason": "verified no GPU use",
            },
        )
    assert response.status_code == 404
