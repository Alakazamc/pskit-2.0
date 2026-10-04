"""Management privilege comes from current server roles, never identity metadata."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_user_cannot_self_assign_admin(tmp_path):
    from app.adapters.live.supabase_auth import SupabaseIdentityAdapter

    app = create_app(Settings(agent_db_path=str(tmp_path / "roles.db")))

    def identity_response(request):
        return httpx.Response(
            200,
            json={
                "id": "forged-member",
                "email": "member@example.org",
                "is_anonymous": False,
                "user_metadata": {"admin": True, "roles": ["platform_admin"], "name": "Fake Admin"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(identity_response)
    ) as identity_client:
        app.state.identity_provider = SupabaseIdentityAdapter(
            "http://supabase", "publishable", identity_client
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.get(
                "/api/v1/admin/me",
                headers={"Authorization": "Bearer verified-with-edited-metadata"},
            )
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_revoked_admin_is_rejected_with_old_token(tmp_path):
    from app.api.admin_auth import router
    from app.domain.admin.roles import AdminStore

    app = create_app(Settings(agent_db_path=str(tmp_path / "roles.db")))
    app.state.admin_store = AdminStore(
        str(tmp_path / "roles.db"),
        identity_policy=app.state.identity_policy,
        quotas=app.state.quotas,
    )
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        user_id = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(
            user_id, "platform_admin", actor="test-bootstrap", reason="Controlled test grant"
        )
        assert (await client.get("/api/v1/admin/me", headers=headers)).status_code == 200
        app.state.admin_store.revoke(
            user_id, "platform_admin", actor="test-bootstrap", reason="Controlled test revoke"
        )
        result = await client.get("/api/v1/admin/me", headers=headers)
        assert result.status_code == 403


@pytest.mark.asyncio
async def test_maintainer_scope_does_not_grant_quota_write(tmp_path):
    from app.api.admin_auth import require_permission
    from app.domain.admin.roles import AdminStore

    app = create_app(Settings(agent_db_path=str(tmp_path / "roles.db")))
    app.state.admin_store = AdminStore(
        str(tmp_path / "roles.db"),
        identity_policy=app.state.identity_policy,
        quotas=app.state.quotas,
    )
    from fastapi import Depends

    @app.post("/test-quota-write", dependencies=[Depends(require_permission("quotas:write"))])
    def write():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "maintainer@example.org"})
        ).json()
        app.state.admin_store.grant(
            login["user"]["id"],
            "service_maintainer",
            service_id="rna",
            actor="bootstrap",
            reason="Controlled scoped grant",
        )
        result = await client.post(
            "/test-quota-write", headers={"Authorization": f"Bearer {login['access_token']}"}
        )
    assert result.status_code == 403
