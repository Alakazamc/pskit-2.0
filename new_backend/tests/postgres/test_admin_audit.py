"""Quota revision, live resource holds, and audit commit as one transaction."""

import httpx
import pytest


def limits(revision=0):
    return {
        "expected_revision": revision,
        "reason": "Set reviewed user limits",
        "token_monthly_limit": 100,
        "gpu_daily_minutes": 1,
        "cpu_daily_core_ms": 60000,
        "concurrency_limit": 2,
        "storage_limit_bytes": 1024,
    }


@pytest.mark.asyncio
async def test_limit_update_is_atomic_and_audited(admin_system):
    app, database = admin_system
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        before = (await client.get("/api/v1/admin/users/member/limits", headers=headers)).json()
        with database.connection() as connection:
            connection.execute(
                "CREATE FUNCTION reject_limit_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='quotas:write' THEN RAISE EXCEPTION 'audit unavailable'; END IF; RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER reject_limit_audit BEFORE INSERT ON admin_audit_events FOR EACH ROW EXECUTE FUNCTION reject_limit_audit()"
            )
        with pytest.raises(Exception, match="audit unavailable"):
            await client.put("/api/v1/admin/users/member/limits", headers=headers, json=limits())
        assert (
            await client.get("/api/v1/admin/users/member/limits", headers=headers)
        ).json() == before
        with database.connection() as connection:
            connection.execute("DROP TRIGGER reject_limit_audit ON admin_audit_events")
        saved = await client.put(
            "/api/v1/admin/users/member/limits", headers=headers, json=limits()
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        events = (await client.get("/api/v1/admin/audit-events", headers=headers)).json()["items"]
        assert any(e["action"] == "quotas:write" and e["resource_id"] == "member" for e in events)
        conflict = await client.put(
            "/api/v1/admin/users/member/limits", headers=headers, json=limits()
        )
        assert conflict.status_code == 409
