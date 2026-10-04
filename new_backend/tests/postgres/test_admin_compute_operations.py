"""Admin actions use real B jobs and preserve holds until trusted stop confirmation."""

import httpx
import pytest

from app.api import compute
from app.contracts.compute import (
    CapabilityVersion,
    Completed,
    ComputeBudget,
    ComputeClaimRequest,
    ComputeResultRequest,
    ComputeServiceManifest,
    UsageReport,
    WorkerResources,
)
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.leases import ComputeLeases
from app.domain.compute.ledger import ComputeLedger


def configure(app, database):
    ledger = ComputeLedger(database, gpu_daily_limit_ms=60000)
    jobs = ComputeJobs(database, ledger)
    jobs.catalog.register(
        ComputeServiceManifest(
            service_id="gpu",
            model_version="v1",
            capabilities=[
                CapabilityVersion(
                    id="gpu.predict",
                    version="1",
                    visibility="published",
                    input_schema={"type": "object"},
                    gpu_count=1,
                    required_usage=["gpu_device_ms"],
                    max_budget=ComputeBudget(gpu_device_ms=60000),
                )
            ],
        )
    )
    app.state.compute_jobs = jobs
    app.state.compute_leases = ComputeLeases(database, ledger, lease_seconds=1)
    app.state.admin_operations.compute_jobs = jobs
    app.state.admin_operations.compute_ledger = ledger
    app.include_router(compute.router)
    return app.state.compute_leases


@pytest.mark.asyncio
async def test_cancel_ui_status_is_not_stopped_until_confirmed(admin_system):
    app, database = admin_system
    leases = configure(app, database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified", "Idempotency-Key": "cancel-job"}
        job = (
            await client.post(
                "/api/v1/compute/jobs",
                headers=h,
                json={
                    "capability_id": "gpu.predict",
                    "version": "1",
                    "arguments": {},
                    "budget": {"gpu_device_ms": 40000},
                },
            )
        ).json()
        grant = leases.claim(
            ComputeClaimRequest(
                service_id="gpu", worker_id="worker", resources=WorkerResources(gpu_uuids=["gpu-1"])
            )
        )
        current = (await client.get("/api/v1/admin/jobs", headers=h)).json()["items"][0]
        cancelled = await client.post(
            f"/api/v1/admin/jobs/{job['id']}/cancel",
            headers=h,
            json={
                "expected_revision": current["revision"],
                "reason": "User requested execution stop",
            },
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "requested"
        usage = (await client.get("/api/v1/compute/usage", headers=h)).json()
        assert usage["gpu"]["reserved"] == 40000
        leases.complete(
            "gpu",
            job["id"],
            ComputeResultRequest(
                worker_id="worker",
                attempt=grant.attempt,
                fencing_token=grant.fencing_token,
                seq=1,
                report=Completed(
                    result={}, usage=UsageReport(gpu_device_ms=15000, source="service_reported")
                ),
                stopped=True,
            ),
        )
        listed = (await client.get("/api/v1/admin/jobs", headers=h)).json()["items"][0]
        assert listed["status"] == "cancelled" and listed["cancellation_state"] == "confirmed"
        assert (await client.get("/api/v1/compute/usage", headers=h)).json()["gpu"]["reserved"] == 0


@pytest.mark.asyncio
async def test_reconcile_requires_reason_and_terminal_evidence(admin_system):
    app, database = admin_system
    leases = configure(app, database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified", "Idempotency-Key": "unknown-job"}
        job = (
            await client.post(
                "/api/v1/compute/jobs",
                headers=h,
                json={
                    "capability_id": "gpu.predict",
                    "version": "1",
                    "arguments": {},
                    "budget": {"gpu_device_ms": 40000},
                },
            )
        ).json()
        leases.claim(
            ComputeClaimRequest(
                service_id="gpu", worker_id="worker", resources=WorkerResources(gpu_uuids=["gpu-1"])
            )
        )
        import asyncio

        await asyncio.sleep(1.05)
        leases.recover_expired()
        current = (await client.get("/api/v1/admin/usage/reconciliation", headers=h)).json()[
            "items"
        ][0]
        path = f"/api/v1/admin/compute/jobs/{job['id']}/reconcile"
        body = {
            "expected_revision": current["revision"],
            "reason": "Stopped executor and recovered journal",
            "terminal_status": "failed",
            "usage": {"gpu_device_ms": 12000, "source": "service_reported"},
            "stopped": False,
            "evidence": "Supervisor stop receipt 42",
        }
        assert (await client.post(path, headers=h, json=body)).status_code == 422
        assert (await client.get("/api/v1/compute/usage", headers=h)).json()["gpu"][
            "reserved"
        ] == 40000
        body["stopped"] = True
        body["evidence"] = ""
        assert (await client.post(path, headers=h, json=body)).status_code == 422
        body["evidence"] = "Supervisor stop receipt 42"
        result = await client.post(path, headers=h, json=body)
        assert result.status_code == 200
        assert result.json()["status"] == "failed"
        assert (await client.get("/api/v1/compute/usage", headers=h)).json()["gpu"]["used"] == 12000
        assert (await client.get("/api/v1/compute/usage", headers=h)).json()["gpu"]["reserved"] == 0


@pytest.mark.asyncio
async def test_worker_state_change_invalidates_admin_revision(admin_system):
    app, database = admin_system
    leases = configure(app, database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified", "Idempotency-Key": "stale-job"}
        job = (
            await client.post(
                "/api/v1/compute/jobs",
                headers=h,
                json={
                    "capability_id": "gpu.predict",
                    "version": "1",
                    "arguments": {},
                    "budget": {"gpu_device_ms": 40000},
                },
            )
        ).json()
        before = (await client.get("/api/v1/admin/jobs", headers=h)).json()["items"][0]
        leases.claim(
            ComputeClaimRequest(
                service_id="gpu", worker_id="worker", resources=WorkerResources(gpu_uuids=["gpu-1"])
            )
        )
        rejected = await client.post(
            f"/api/v1/admin/jobs/{job['id']}/cancel",
            headers=h,
            json={
                "expected_revision": before["revision"],
                "reason": "Cancel from stale operator view",
            },
        )
        assert rejected.status_code == 409
        assert (await client.get(f"/api/v1/compute/jobs/{job['id']}", headers=h)).json()[
            "status"
        ] == "running"


@pytest.mark.asyncio
async def test_default_cpu_limits_match_actual_compute_admission(admin_system):
    app, database = admin_system
    ledger = ComputeLedger(database, cpu_daily_limit_ms=60000, gpu_daily_limit_ms=60000)
    app.state.admin_operations.compute_ledger = ledger
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/admin/users/member/limits", headers={"Authorization": "Bearer verified"}
        )
    assert response.status_code == 200
    assert response.json()["cpu_daily_core_ms"] == 60000
    assert response.json()["cpu"] == ledger.usage_for("member").cpu.model_dump()


@pytest.mark.asyncio
async def test_lower_limit_keeps_existing_usage_and_reservation(admin_system):
    app, database = admin_system
    configure(app, database)
    app.state.admin_store.quotas.charge_tokens("operator", 7)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified", "Idempotency-Key": "keep-reservation"}
        created = await client.post(
            "/api/v1/compute/jobs",
            headers=h,
            json={
                "capability_id": "gpu.predict",
                "version": "1",
                "arguments": {},
                "budget": {"gpu_device_ms": 40000},
            },
        )
        assert created.status_code == 200
        changed = await client.put(
            "/api/v1/admin/users/operator/limits",
            headers=h,
            json={
                "expected_revision": 0,
                "reason": "Lower future admissions only",
                "token_monthly_limit": 4,
                "gpu_daily_minutes": 0,
                "cpu_daily_core_ms": 0,
                "concurrency_limit": 1,
                "storage_limit_bytes": 1024,
            },
        )
        assert changed.status_code == 200
        assert changed.json()["tokens"]["used"] == 7
        assert changed.json()["tokens"]["remaining"] == 0
        actual = (await client.get("/api/v1/compute/usage", headers=h)).json()
        assert actual["gpu"]["limit"] == 0
        assert actual["gpu"]["reserved"] == 40000
        assert actual["gpu"]["used"] == 0
        assert actual["gpu"]["remaining"] == 0
