"""Exercise management through the ordinary authenticated application boundary."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_management_is_available_to_verified_identity_without_exposing_operator_secret():
    app = create_app(Settings(admin_api_key="private-operator-secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/auth/demo", json={"email": "member@example.org"})
        identity = login.json()
        denied = await client.get(
            "/api/v1/admin/me",
            headers={
                "Authorization": f"Bearer {identity['access_token']}",
            },
        )
        unauthenticated = await client.get("/api/v1/admin/me")
    assert denied.status_code == 403
    assert denied.json() == {"detail": {"code": "ADMIN_FORBIDDEN"}}
    assert unauthenticated.status_code == 401
    assert "private-operator-secret" not in denied.text + unauthenticated.text


@pytest.mark.asyncio
async def test_managed_catalog_starts_empty_without_changing_legacy_mode():
    for mode, expected_ids in (("managed", []), ("legacy", ["mock-model"])):
        app = create_app(Settings(model_policy_mode=mode))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            identity = (
                await client.post("/api/v1/auth/demo", json={"email": "member@example.org"})
            ).json()
            response = await client.get(
                "/api/v1/models",
                headers={
                    "Authorization": f"Bearer {identity['access_token']}",
                },
            )
        assert response.status_code == 200
        assert [model["id"] for model in response.json()] == expected_ids


def test_invalid_model_policy_mode_cannot_silently_fall_back_to_open_catalog(monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_MODEL_POLICY_MODE", "managd")
    with pytest.raises(ValueError, match="MODEL_POLICY_MODE"):
        create_app(Settings.from_env())


@pytest.mark.parametrize(
    "field", ["admin_service_endpoints_json", "admin_service_credentials_json"]
)
def test_approved_service_references_require_an_object_config(field):
    with pytest.raises(ValueError, match="ADMIN_SERVICE"):
        create_app(Settings(**{field: "[]"}))


def test_server_approved_endpoint_can_include_a_discovery_allowlist():
    app = create_app(
        Settings(
            admin_service_endpoints_json='{"lab": {"url": "http://lab:8080", "allowed_tools": ["predict"]}}'
        )
    )
    assert app.state.admin_releases.endpoints["lab"]["allowed_tools"] == ["predict"]


@pytest.mark.asyncio
async def test_full_app_exposes_audited_management_and_unavailable_sandbox_truthfully():
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        identity = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        user_id = identity["user"]["id"]
        app.state.admin_store.grant(
            user_id, "platform_admin", actor="test-bootstrap", reason="Controlled integration test"
        )
        headers = {"Authorization": f"Bearer {identity['access_token']}"}
        for path in ("services", "jobs", "usage/reconciliation", "audit-events"):
            response = await client.get(f"/api/v1/admin/{path}", headers=headers)
            assert response.status_code == 200, (path, response.text)
            assert isinstance(response.json()["items"], list)
        users = await client.get("/api/v1/admin/users", headers=headers)
        assert users.status_code == 200
        assert users.json()["items"][0]["user_id"] == user_id
        unavailable = await client.get("/api/v1/admin/sandboxes", headers=headers)
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "SANDBOX_OPERATIONS_UNAVAILABLE"
        files = await client.post(
            "/api/v1/sandbox/sessions/missing/files", headers=headers, json={"file_ids": []}
        )
        assert files.status_code == 503


def test_live_sandbox_artifacts_obey_the_same_configured_storage_limit(live_database):
    dsn, schema = live_database
    app = create_app(
        Settings(
            mode="live",
            agent_runtime="pi",
            database_url=dsn,
            database_schema=schema,
            supabase_url="http://auth",
            supabase_publishable_key="test-only",
            model_gateway_base_url="http://gateway/v1",
            model_gateway_model="test-model",
            pi_execution="sandbox",
            sandbox_manager_url="http://manager",
            sandbox_manager_token="test-only-manager-token",
        ),
        pi_runner=object(),
    )
    from app.contracts.admin import LimitsUpdate
    from app.domain.catalog import InvalidFileUpload

    app.state.identity_policy.observe_verified_user("member", False)
    app.state.admin_operations.update_limits(
        app.state.admin_store.principal("server:test"),
        "member",
        LimitsUpdate(
            expected_revision=0,
            reason="Storage admission integration",
            token_monthly_limit=100,
            gpu_daily_minutes=0,
            storage_limit_bytes=4,
        ),
    )
    app.state.catalog.add_uploaded_file("member", "input.txt", b"four")
    with pytest.raises(InvalidFileUpload):
        app.state.workspace_transfer.artifacts.put(
            "member", "session", "attempt", "output.txt", b"x"
        )


@pytest.mark.asyncio
async def test_invalid_admin_body_returns_stable_error_without_reflecting_input():
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        identity = (
            await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})
        ).json()
        app.state.admin_store.grant(
            identity["user"]["id"],
            "platform_admin",
            actor="server:test",
            reason="Controlled validation test",
        )
        response = await client.put(
            "/api/v1/admin/llm-aliases/mock-model/draft",
            headers={
                "Authorization": f"Bearer {identity['access_token']}",
            },
            json={"expected_revision": "private-test-input", "reason": "short"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "ADMIN_VALIDATION_FAILED"}}
    assert "private-test-input" not in response.text
