"""Management acts on actual legacy AF3 jobs without fabricating stop confirmation."""

import httpx
import pytest

from app.contracts.capabilities import Af3FoldInput, ComputeWorkerResources

FOLD_INPUT = Af3FoldInput.model_validate(
    {
        "name": "legacy protein",
        "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "PVLSCGEWQL"}}],
        "dialect": "alphafold3",
        "version": 4,
    }
)
RESOURCES = ComputeWorkerResources(capabilities={"af3"}, gpu_count=1, gpu_memory_mb=49152)


def legacy_job(app, *, started):
    store = app.state.admin_store.quotas
    job = store.create_af3_job("member", 20, fold_input=FOLD_INPUT)
    if started:
        assert store.claim_compute_jobs("legacy-worker", RESOURCES)[0].id == job.id
    return store, job


@pytest.mark.asyncio
async def test_cancelled_pending_legacy_job_is_not_reported_stopped(admin_system):
    app, _ = admin_system
    store, job = legacy_job(app, started=True)
    assert store.cancel_af3_job("member", job.id).gpu_accounting_status == "pending_reconciliation"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        rows = (
            await client.get("/api/v1/admin/jobs", headers={"Authorization": "Bearer verified"})
        ).json()["items"]
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["accounting_status"] == "pending_reconciliation"
    assert rows[0]["cancellation_state"] == "requested"
    assert store.usage_for("member").gpu.reserved == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [True, False])
async def test_admin_cancel_dispatches_actual_legacy_operation(admin_system, started):
    app, _ = admin_system
    store, job = legacy_job(app, started=started)
    app.state.admin_store.revoke(
        "operator", "platform_admin", actor="server:test", reason="Test quota operator authority"
    )
    app.state.admin_store.grant(
        "operator", "quota_operator", actor="server:test", reason="Test quota operator authority"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        row = (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][0]
        result = await client.post(
            f"/api/v1/admin/jobs/{job.id}/cancel",
            headers=headers,
            json={
                "expected_revision": row["revision"],
                "reason": "Request actual legacy worker stop",
            },
        )
        assert result.status_code == 200
        assert result.json()["state"] == ("requested" if started else "confirmed")
        after = (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][0]
        assert after["cancellation_state"] == ("requested" if started else "confirmed")
        assert after["accounting_status"] == ("pending_reconciliation" if started else "released")
        assert after["revision"] == result.json()["revision"]
        assert after["revision"] > row["revision"]
        assert store.usage_for("member").gpu.reserved == (20 if started else 0)
        app.state.admin_store.grant(
            "operator",
            "platform_admin",
            actor="server:test",
            reason="Read management audit records",
        )
        events = (await client.get("/api/v1/admin/audit-events", headers=headers)).json()["items"]
        assert any(
            event["action"] == "jobs:cancel" and event["resource_id"] == job.id for event in events
        )


@pytest.mark.asyncio
async def test_legacy_cancel_audit_failure_rolls_back_job_and_hold(admin_system):
    app, database = admin_system
    store, job = legacy_job(app, started=True)
    with database.connection() as connection:
        connection.execute(
            "CREATE FUNCTION reject_legacy_cancel_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='jobs:cancel' THEN RAISE EXCEPTION 'legacy cancel audit unavailable'; END IF; RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER reject_legacy_cancel_audit BEFORE INSERT ON admin_audit_events FOR EACH ROW EXECUTE FUNCTION reject_legacy_cancel_audit()"
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        before = (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][0]
        with pytest.raises(Exception, match="legacy cancel audit unavailable"):
            await client.post(
                f"/api/v1/admin/jobs/{job.id}/cancel",
                headers=headers,
                json={
                    "expected_revision": before["revision"],
                    "reason": "Request actual legacy worker stop",
                },
            )
        assert (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][
            0
        ] == before
        assert store.usage_for("member").gpu.reserved == 20


@pytest.mark.asyncio
async def test_native_legacy_reconciliation_requires_stop_evidence_and_fences_revision(
    admin_system,
):
    app, _ = admin_system
    store, job = legacy_job(app, started=True)
    store.cancel_af3_job("member", job.id)
    app.state.admin_store.revoke(
        "operator", "platform_admin", actor="server:test", reason="Test quota operator authority"
    )
    app.state.admin_store.grant(
        "operator", "quota_operator", actor="server:test", reason="Test quota operator authority"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        row = (await client.get("/api/v1/admin/usage/reconciliation", headers=headers)).json()[
            "items"
        ][0]
        path = f"/api/v1/admin/af3/jobs/{job.id}/reconcile"
        body = {
            "expected_revision": row["revision"],
            "reason": "Reconcile native legacy worker journal",
            "actual_gpu_minutes": 9,
            "stopped": True,
            "evidence": "Supervisor receipt confirms legacy worker stopped",
            "source": "legacy_wall",
        }
        for changed in [
            {"stopped": False},
            {"evidence": ""},
            {"reason": ""},
            {"actual_gpu_minutes": False},
            {"source": "measured"},
        ]:
            assert (
                await client.post(path, headers=headers, json={**body, **changed})
            ).status_code == 422
            assert store.usage_for("member").gpu.reserved == 20
        stale = await client.post(
            path, headers=headers, json={**body, "expected_revision": row["revision"] - 1}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
        assert store.usage_for("member").gpu.reserved == 20
        result = await client.post(path, headers=headers, json=body)
        assert result.status_code == 200
        assert result.json()["status"] == "cancelled"
        assert result.json()["accounting_status"] == "reconciled"
        assert result.json()["cancellation_state"] == "confirmed"
        assert result.json()["revision"] > row["revision"]
        assert store.usage_for("member").gpu.used == 9
        assert store.usage_for("member").gpu.reserved == 0
        app.state.admin_store.grant(
            "operator",
            "platform_admin",
            actor="server:test",
            reason="Read native reconciliation audit",
        )
        events = (await client.get("/api/v1/admin/audit-events", headers=headers)).json()["items"]
        event = next(
            event
            for event in events
            if event["action"] == "usage:reconcile-af3" and event["resource_id"] == job.id
        )
        assert event["after"]["source"] == "legacy_wall"
        assert event["after"]["actual_gpu_minutes"] == 9
        assert event["after"]["evidence"] == body["evidence"]


@pytest.mark.asyncio
async def test_native_legacy_reconcile_audit_failure_rolls_back_usage_and_history(admin_system):
    app, database = admin_system
    store, job = legacy_job(app, started=True)
    store.cancel_af3_job("member", job.id)
    with database.connection() as connection:
        connection.execute(
            "CREATE FUNCTION reject_native_af3_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='usage:reconcile-af3' THEN RAISE EXCEPTION 'legacy reconcile audit unavailable'; END IF; RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER reject_native_af3_audit BEFORE INSERT ON admin_audit_events FOR EACH ROW EXECUTE FUNCTION reject_native_af3_audit()"
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        before = (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][0]
        with pytest.raises(Exception, match="legacy reconcile audit unavailable"):
            await client.post(
                f"/api/v1/admin/af3/jobs/{job.id}/reconcile",
                headers=headers,
                json={
                    "expected_revision": before["revision"],
                    "reason": "Reconcile native worker stop receipt",
                    "actual_gpu_minutes": 9,
                    "stopped": True,
                    "evidence": "Supervisor receipt confirms legacy worker stopped",
                    "source": "legacy_wall",
                },
            )
        assert (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][
            0
        ] == before
        assert store.usage_for("member").gpu.reserved == 20
        assert store.usage_for("member").gpu.used == 0
        assert store.gpu_reconciliations_for(job.id) == []


@pytest.mark.asyncio
async def test_jwt_cannot_bypass_terminal_evidence_via_legacy_key_endpoint(admin_system):
    app, _ = admin_system
    store, job = legacy_job(app, started=True)
    app.state.conversations = store
    store.cancel_af3_job("member", job.id)
    app.state.admin_store.revoke(
        "operator", "platform_admin", actor="server:test", reason="Test quota operator authority"
    )
    app.state.admin_store.grant(
        "operator", "quota_operator", actor="server:test", reason="Test quota operator authority"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {
            "Authorization": "Bearer verified",
            "X-Admin-Key": "legacy-key-cannot-bypass-jwt",
        }
        before = (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][0]
        usage = store.usage_for("member")
        denied = await client.put(
            f"/api/v1/admin/af3/jobs/{job.id}/gpu-usage",
            headers=headers,
            json={"actual_gpu_minutes": 9, "reason": "Weak payload omits stop and evidence"},
        )
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "USE_AF3_RECONCILIATION_ENDPOINT"
        assert (await client.get("/api/v1/admin/jobs", headers=headers)).json()["items"][
            0
        ] == before
        assert store.usage_for("member") == usage
        assert store.gpu_reconciliations_for(job.id) == []
        strong = await client.post(
            f"/api/v1/admin/af3/jobs/{job.id}/reconcile",
            headers=headers,
            json={
                "expected_revision": before["revision"],
                "actual_gpu_minutes": 9,
                "reason": "Verified native worker stop receipt",
                "stopped": True,
                "evidence": "Supervisor receipt confirms actual terminal stop",
                "source": "legacy_wall",
            },
        )
        assert strong.status_code == 200
        app.state.admin_store.grant(
            "operator", "auditor", actor="server:test", reason="Read safe native accounting audit"
        )
        history = await client.get(
            f"/api/v1/admin/af3/jobs/{job.id}/gpu-usage/audit", headers=headers
        )
        assert history.status_code == 200
        assert history.json()[0]["actual_minutes"] == 9
        assert set(history.json()[0]) == {
            "id",
            "job_id",
            "user_id",
            "source",
            "previous_minutes",
            "actual_minutes",
            "reason",
            "created_at",
        }
