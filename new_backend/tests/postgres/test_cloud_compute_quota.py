"""Cloud quota configuration must reach the real Tool Product admission path."""

from dataclasses import replace
from pathlib import Path

import pytest
from deploy.agent.tests.test_gpu_quota_compose import render_backend
from fastapi.testclient import TestClient
from test_tool_product_repository import report_for
from test_tool_product_runs import product_payload, seed_coral_endpoint, start

from app.api.auth import get_current_user
from app.config import Settings
from app.contracts.models import UserIdentity
from app.contracts.tool_products import ToolProductDraft
from app.db.postgres_migrations import migrate_postgres
from app.main import create_app


@pytest.fixture
def cloud_compute_api(pg_schema, monkeypatch, tmp_path):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    template = Path(__file__).resolve().parents[3] / "deploy/agent/cloud.backend.env.example"
    values = dict(line.split("=", 1) for line in template.read_text().splitlines()
                  if line and not line.startswith("#") and "=" in line)
    environment = render_backend(tmp_path, ["compose.postgres.yaml"], 60)
    # Read the actual merged Compose environment, never a diagnostic-only 60-minute fallback.
    for key in ("RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES", "RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES"):
        monkeypatch.setenv(key, environment[key])
    app = create_app(replace(
        Settings.from_env(), mode="live", agent_runtime="pi",
        database_url=dsn, database_schema=schema,
        supabase_url="https://identity.invalid", supabase_publishable_key="test-only",
        model_gateway_base_url="https://gateway.invalid/v1", model_gateway_model="test-model",
        model_gateway_api_key="test-only", mcp_executor="disabled", af3_executor="disabled",
        compute_enabled=True, compute_services_json="[]",
        compute_cpu_daily_limit_ms=int(values["RESEARCH_AGENT_COMPUTE_CPU_DAILY_LIMIT_MS"]),
        auth_csrf_secret="test-only-csrf-secret-at-least-32-characters",
    ), pi_runner=object())
    database = app.state.database
    try:
        seed_coral_endpoint(database)
        payload = product_payload()
        payload["bindings"][0].update(
            required_usage=["cpu_core_ms", "gpu_device_ms"], gpu_count=1,
            max_budget={"cpu_core_ms": 120_000, "gpu_device_ms": 120_000},
        )
        repository = app.state.tool_product_repository
        draft = repository.save_draft(
            "owner", payload["product_id"], ToolProductDraft.model_validate(payload), 0,
        )
        report = report_for(draft)
        repository.record_qualification(report)
        repository.publish(draft.product_id, draft.revision, report.report_id, "publisher")
        actor = {"id": "alice", "anonymous": False}

        def current_user():
            app.state.identity_policy.observe_verified_user(actor["id"], actor["anonymous"])
            return UserIdentity(id=actor["id"], email=f"{actor['id']}@example.org",
                                name=actor["id"], is_anonymous=actor["anonymous"])

        app.dependency_overrides[get_current_user] = current_user
        # No lifespan: no Pi process, remote API call or compute worker is started.
        yield app, actor, TestClient(app)
    finally:
        database.close()


def test_cloud_member_gpu_default_admits_coral_and_reserves_same_usage(cloud_compute_api):
    app, _actor, client = cloud_compute_api
    before = client.get("/api/v1/usage").json()
    response = start(client)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    after = client.get("/api/v1/usage").json()
    assert before["gpu"]["limit"] == after["gpu"]["limit"] == 60
    assert after["gpu"]["reserved"] == 2
    counters = app.state.compute_jobs.ledger.usage_for("alice")
    assert counters.gpu.limit == 3_600_000
    assert counters.gpu.used == 0
    assert counters.gpu.reserved == 120_000
    assert counters.gpu.remaining == 3_480_000
    assert counters.cpu.reserved == 120_000


def test_explicit_zero_gpu_quota_remains_authoritative(cloud_compute_api):
    app, _actor, client = cloud_compute_api
    app.state.conversations.set_gpu_limit("alice", 0)
    response = start(client)
    assert response.status_code == 429
    assert response.json() == {"detail": {"code": "GPU_QUOTA_EXCEEDED"}}
    assert app.state.compute_jobs.ledger.usage_for("alice").gpu.reserved == 0
    with app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM tool_product_runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM compute_reservations").fetchone()[0] == 0


def test_cloud_guest_still_has_no_gpu_access(cloud_compute_api):
    app, actor, client = cloud_compute_api
    actor.update(id="guest", anonymous=True)
    usage = client.get("/api/v1/usage").json()
    response = start(client)
    assert usage["gpu"]["limit"] == 0
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "LOGIN_REQUIRED"}}
    assert app.state.compute_jobs.ledger.usage_for("guest").gpu.reserved == 0
