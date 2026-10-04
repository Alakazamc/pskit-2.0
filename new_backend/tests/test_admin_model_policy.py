"""Publication and revocation govern the current user's visible model catalog."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.services.model_catalog import ModelCatalog


def policy_app(tmp_path):
    from app.api import admin_auth, admin_models
    from app.domain.admin.model_policy import ModelPolicy
    from app.domain.admin.roles import AdminStore

    app = create_app(Settings(agent_db_path=str(tmp_path / "models.db")))
    store = AdminStore(str(tmp_path / "models.db"))
    app.state.admin_store = store

    def gateway(request):
        return httpx.Response(200, json={"data": [{"id": "lab-chat"}]})

    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway",
        api_key="server-only",
        default_model="lab-chat",
        transport=httpx.MockTransport(gateway),
    )
    app.state.model_policy = ModelPolicy(store, app.state.model_catalog, managed=True)
    app.include_router(admin_auth.router)
    app.include_router(admin_models.router)
    return app


@pytest.mark.asyncio
async def test_new_gateway_alias_is_draft_until_published(tmp_path):
    app = policy_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="server:test", reason="Test grant")
        assert (await client.get("/api/v1/models", headers=headers)).json() == []
        draft = await client.put(
            "/api/v1/admin/llm-aliases/lab-chat/draft",
            headers=headers,
            json={
                "expected_revision": 0,
                "reason": "Approved chat users",
                "allowed_user_ids": [uid],
                "purposes": ["chat"],
            },
        )
        assert draft.status_code == 200
        assert (await client.get("/api/v1/models", headers=headers)).json() == []
        published = await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/publish",
            headers=headers,
            json={"expected_revision": 1, "reason": "Reviewed alias release"},
        )
        assert published.status_code == 200
        assert [m["id"] for m in (await client.get("/api/v1/models", headers=headers)).json()] == [
            "lab-chat"
        ]
        assert "server-only" not in published.text


@pytest.mark.asyncio
async def test_policy_revocation_applies_even_with_cached_gateway(tmp_path):
    app = policy_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="server:test", reason="Test grant")
        await client.put(
            "/api/v1/admin/llm-aliases/lab-chat/draft",
            headers=headers,
            json={
                "expected_revision": 0,
                "reason": "Approved chat users",
                "allowed_user_ids": [uid],
                "purposes": ["chat"],
            },
        )
        await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/publish",
            headers=headers,
            json={"expected_revision": 1, "reason": "Reviewed alias release"},
        )
        assert [m["id"] for m in (await client.get("/api/v1/models", headers=headers)).json()] == [
            "lab-chat"
        ]
        retired = await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/retire",
            headers=headers,
            json={"expected_revision": 2, "reason": "Emergency policy revoke"},
        )
        assert retired.status_code == 200
        assert (await client.get("/api/v1/models", headers=headers)).json() == []
        with pytest.raises(PermissionError):
            await app.state.model_policy.authorize(uid, "lab-chat", "chat")


@pytest.mark.asyncio
async def test_managed_catalog_does_not_claim_fallback_gateway_availability(tmp_path):
    app = policy_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        h = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="server:test", reason="Test grant")
        await client.put(
            "/api/v1/admin/llm-aliases/lab-chat/draft",
            headers=h,
            json={
                "expected_revision": 0,
                "reason": "Approved gateway alias",
                "allowed_user_ids": [uid],
            },
        )
        await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/publish",
            headers=h,
            json={"expected_revision": 1, "reason": "Publish gateway alias"},
        )
        from app.domain.admin.model_policy import ModelPolicy

        app.state.model_catalog = ModelCatalog(
            base_url="http://gateway",
            api_key="server-only",
            default_model="lab-chat",
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )
        app.state.model_policy = ModelPolicy(
            app.state.admin_store, app.state.model_catalog, managed=True
        )
        assert (await client.get("/api/v1/models", headers=h)).json() == []
        assert (await client.get("/api/v1/admin/llm-aliases", headers=h)).json()["items"][0][
            "gateway_available"
        ] is False
