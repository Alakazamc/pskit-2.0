"""Service maintenance scope and bounded discovery use the management HTTP seam."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


def service_app(tmp_path):
    from app.api import admin_services
    from app.domain.admin.releases import ConfigReleaseService
    from app.domain.admin.roles import AdminStore

    app = create_app(Settings(agent_db_path=str(tmp_path / "services.db")))
    app.state.admin_store = AdminStore(str(tmp_path / "services.db"))
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={
            "lab": {"url": "http://lab", "schema_path": "/schema", "health_path": "/health"}
        },
        transport=httpx.MockTransport(upstream),
    )
    app.include_router(admin_services.router)
    return app, calls


def draft(owner):
    return {
        "expected_revision": 0,
        "reason": "Submit owned scientific service",
        "name": "RNA",
        "owner_user_id": owner,
        "transport": "http",
        "endpoint_ref": "lab",
        "model_version": "v1",
        "capabilities": [{"id": "rna.predict", "version": "1", "input_schema": {"type": "object"}}],
    }


@pytest.mark.asyncio
async def test_maintainer_can_edit_only_owned_service(tmp_path):
    app, _ = service_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "maintainer@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(
            uid, "service_maintainer", service_id="rna", actor="bootstrap", reason="Scope grant"
        )
        assert (
            await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=draft(uid))
        ).status_code == 200
        assert (
            await client.put("/api/v1/admin/services/other/draft", headers=headers, json=draft(uid))
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/admin/config-releases",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "reason": "Attempt release",
                    "services": [{"service_id": "rna", "revision": 1}],
                },
            )
        ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["draft", "checks", "discovery"])
async def test_auditor_role_does_not_widen_maintainer_write_scope(tmp_path, action):
    app, calls = service_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        bob_id = bob["user"]["id"]
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        app.state.admin_store.grant(
            bob_id, "platform_admin", actor="bootstrap", reason="Admin grant"
        )
        assert (
            await client.put(
                "/api/v1/admin/services/victim/draft", headers=bob_headers, json=draft(bob_id)
            )
        ).status_code == 200
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        alice_id = alice["user"]["id"]
        headers = {"Authorization": f"Bearer {alice['access_token']}"}
        app.state.admin_store.grant(
            alice_id,
            "service_maintainer",
            service_id="rna",
            actor="bootstrap",
            reason="Own service scope",
        )
        app.state.admin_store.grant(
            alice_id, "auditor", actor="bootstrap", reason="Global audit read"
        )
        # Auditor grants read access across services; it cannot widen write scope.
        before = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"]
        assert before[0]["service_id"] == "victim"
        if action == "draft":
            body = {**draft(bob_id), "expected_revision": 1}
            denied = await client.put(
                "/api/v1/admin/services/victim/draft", headers=headers, json=body
            )
        else:
            body = {"expected_revision": 1, "reason": "Attempt outside service probe"}
            if action == "checks":
                body["kind"] = "connectivity"
            denied = await client.post(
                f"/api/v1/admin/services/victim/{action}", headers=headers, json=body
            )
        assert denied.status_code == 403
        assert calls == []
        after = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"]
        assert after == before


@pytest.mark.asyncio
async def test_connectivity_check_does_not_run_inference(tmp_path):
    app, calls = service_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="bootstrap", reason="Admin grant")
        await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=draft(uid))
        response = await client.post(
            "/api/v1/admin/services/rna/checks",
            headers=headers,
            json={
                "expected_revision": 1,
                "reason": "Bounded connectivity validation",
                "kind": "connectivity",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "passed"
        assert calls == [("GET", "/health")]
        listed = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][0]
        assert listed["state"] == "draft"


@pytest.mark.asyncio
async def test_schema_check_matches_actual_openapi_input_and_output(tmp_path):
    app, _ = service_app(tmp_path)
    schema = {"type": "object"}
    app.state.admin_releases.transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "openapi": "3.1.0",
                "paths": {
                    "/predict": {
                        "post": {
                            "operationId": "rna.predict",
                            "requestBody": {"content": {"application/json": {"schema": schema}}},
                            "responses": {
                                "200": {"content": {"application/json": {"schema": schema}}}
                            },
                        }
                    }
                },
            },
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="bootstrap", reason="Admin grant")
        assert (
            await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=draft(uid))
        ).status_code == 200
        checked = await client.post(
            "/api/v1/admin/services/rna/checks",
            headers=headers,
            json={
                "expected_revision": 1,
                "reason": "Compare actual OpenAPI schemas",
                "kind": "schema",
            },
        )
        assert checked.status_code == 200
        assert checked.json()["status"] == "passed"
        assert checked.json()["discovered_capabilities"][0]["output_schema"] == schema
        listed = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][0]
        assert listed["state"] == "validated"


@pytest.mark.asyncio
async def test_invalid_capability_schema_returns_validation_error(tmp_path):
    app, _ = service_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        h = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(
            uid, "platform_admin", actor="server:test", reason="Admin fixture grant"
        )
        body = draft(uid)
        body["capabilities"][0]["input_schema"] = {"type": "not-a-real-type"}
        response = await client.put("/api/v1/admin/services/rna/draft", headers=h, json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_CAPABILITY_SCHEMA"


@pytest.mark.asyncio
async def test_schema_check_does_not_validate_unobserved_output_schema(tmp_path):
    app, _ = service_app(tmp_path)
    app.state.admin_releases.transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"capabilities": [{"id": "rna.predict", "input_schema": {"type": "object"}}]},
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        uid = login["user"]["id"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(uid, "platform_admin", actor="bootstrap", reason="Admin grant")
        body = draft(uid)
        body["capabilities"][0]["output_schema"] = {
            "type": "object",
            "required": ["prediction"],
            "properties": {"prediction": {"type": "string"}},
        }
        assert (
            await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=body)
        ).status_code == 200
        checked = await client.post(
            "/api/v1/admin/services/rna/checks",
            headers=headers,
            json={"expected_revision": 1, "reason": "Compare observed schemas", "kind": "schema"},
        )
        assert checked.status_code == 200
        assert checked.json()["status"] == "failed"
        listed = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][0]
        assert listed["state"] == "draft"
