"""Current role revocation persists and affects the same token across processes."""

import httpx
import pytest

from app.db.postgres import PostgresDatabase
from app.domain.admin.roles import AdminStore


@pytest.mark.asyncio
async def test_role_revocation_from_second_connection_rejects_existing_token(
    admin_system, pg_schema
):
    app, _ = admin_system
    dsn, schema = pg_schema
    second = PostgresDatabase(dsn, schema=schema)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            h = {"Authorization": "Bearer same-valid-token"}
            initial = await client.get("/api/v1/admin/me", headers=h)
            assert initial.status_code == 200
            assert initial.json()["roles"] == ["platform_admin"]
            other = AdminStore(second)
            other.revoke(
                "operator", "platform_admin", actor="server:second", reason="Current access revoked"
            )
            assert (await client.get("/api/v1/admin/me", headers=h)).status_code == 403
            other.grant("operator", "auditor", actor="server:second", reason="Read access restored")
            assert (await client.get("/api/v1/admin/me", headers=h)).json()["roles"] == ["auditor"]
            events = (await client.get("/api/v1/admin/audit-events", headers=h)).json()["items"]
            assert any(
                e["actor_user_id"] == "server:second" and e["action"] == "roles:revoke"
                for e in events
            )
    finally:
        second.close()


@pytest.mark.asyncio
async def test_role_revocation_requires_a_reason_and_preserves_current_grant(admin_system):
    app, _ = admin_system
    store = app.state.admin_store
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        before = (await client.get("/api/v1/admin/audit-events", headers=headers)).json()
        with pytest.raises(ValueError, match="REASON_REQUIRED"):
            store.revoke("operator", "platform_admin", actor="server:test", reason="  ")
        assert (await client.get("/api/v1/admin/me", headers=headers)).json()["roles"] == [
            "platform_admin"
        ]
        assert (await client.get("/api/v1/admin/audit-events", headers=headers)).json() == before
