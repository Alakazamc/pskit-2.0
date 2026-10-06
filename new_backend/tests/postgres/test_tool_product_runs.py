from copy import deepcopy

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_tool_product_repository import draft_payload, report_for

from app.api.auth import get_current_user
from app.api.tool_products import router, tool_run_router
from app.contracts.compute import (
    CapabilityVersion,
    ComputeBudget,
    ComputeServiceManifest,
)
from app.contracts.models import UserIdentity
from app.contracts.tool_products import ToolProductDraft
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.ledger import ComputeLedger
from app.domain.tool_products.registry import ToolProductRegistry
from app.domain.tool_products.repository import ToolProductRepository
from app.domain.tool_products.runs import ToolRunGateway


def capability(capability_id, *, budget=40):
    return CapabilityVersion(
        id=capability_id,
        version="1.0.0",
        visibility="published",
        input_schema={
            "type": "object",
            "properties": {"protein": {"type": "string", "minLength": 3}},
            "required": ["protein"],
            "additionalProperties": False,
        },
        required_usage=["cpu_core_ms"],
        max_budget=ComputeBudget(cpu_core_ms=budget),
    )


def product_payload(*, two_actions=False):
    payload = draft_payload()
    payload["handoffs"] = [{
        "id": "analyze",
        "label": {"en": "Analyze", "zh-CN": "分析"},
        "prompt_template_id": "tool-result-analysis",
        "summary_pointer": "/run/result",
        "artifact_pointers": ["/run/artifacts"],
    }]
    payload["bindings"][0]["capability_version"] = "1.0.0"
    payload["actions"][0]["input_schema"] = capability("coral.generate.one-shot").input_schema
    if two_actions:
        payload["bindings"].append({
            **deepcopy(payload["bindings"][0]),
            "binding_id": "binding-pocket",
            "product_action_id": "coral-pocket",
            "capability_id": "coral.analyze.pocket",
            "submit_tool": "analyze_protein_pockets",
        })
        payload["actions"].append({
            "id": "coral-pocket",
            "label": {"en": "Pocket", "zh-CN": "口袋"},
            "kind": "capability",
            "binding_ids": ["binding-pocket"],
            "input_schema": capability("coral.analyze.pocket").input_schema,
        })
        payload["ui_schema"]["actions"].append({
            "id": "run-pocket",
            "label": {"en": "Pocket", "zh-CN": "口袋"},
            "kind": "start_run",
            "target": {"action_id": "coral-pocket"},
        })
    return payload


@pytest.fixture
def tool_run_system(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    repository = ToolProductRepository(database)
    catalog = ComputeCatalog(database)
    catalog.register(ComputeServiceManifest(
        service_id="coral",
        model_version="coral-1",
        capabilities=[
            capability("coral.generate.one-shot"),
            capability("coral.analyze.pocket"),
        ],
    ))
    ledger = ComputeLedger(database, cpu_daily_limit_ms=100, gpu_daily_limit_ms=100)
    jobs = ComputeJobs(database, ledger)
    draft = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(product_payload(two_actions=True)), 0
    )
    report = report_for(draft)
    repository.record_qualification(report)
    release = repository.publish(
        draft.product_id, draft.revision, report.report_id, "admin"
    )
    registry = ToolProductRegistry(repository)
    gateway = ToolRunGateway(database, repository, registry, jobs)
    app = FastAPI()
    app.state.tool_product_registry = registry
    app.state.tool_run_gateway = gateway
    app.include_router(router)
    app.include_router(tool_run_router)

    actor = {"id": "alice"}

    def identity():
        return UserIdentity(
            id=actor["id"],
            email=f"{actor['id']}@example.org",
            name=actor["id"],
            is_anonymous=False,
        )

    app.dependency_overrides[get_current_user] = identity
    try:
        yield database, repository, jobs, release, actor, TestClient(app)
    finally:
        database.close()


def start(client, action="coral-one-shot", protein="MKT", key="run-key"):
    return client.post(
        f"/api/v1/tool-products/coral/actions/{action}/runs",
        headers={"Idempotency-Key": key},
        json={"arguments": {"protein": protein}},
    )


def test_start_pins_release_action_capability_and_is_idempotent(tool_run_system):
    database, _repository, _jobs, release, _actor, client = tool_run_system

    first = start(client)
    repeated = start(client)

    assert first.status_code == 200
    assert repeated.json()["run_id"] == first.json()["run_id"]
    assert first.json()["release_id"] == release.release_id
    assert first.json()["action_id"] == "coral-one-shot"
    with database.connection() as connection:
        run = connection.execute(
            "SELECT snapshot_json FROM tool_product_runs WHERE run_id=%s",
            (first.json()["run_id"],),
        ).fetchone()[0]
        steps = connection.execute(
            "SELECT binding_id,compute_job_id FROM tool_product_run_steps WHERE run_id=%s",
            (first.json()["run_id"],),
        ).fetchall()
        count = connection.execute(
            "SELECT count(*) FROM compute_job_data WHERE user_id='alice'"
        ).fetchone()[0]
        with pytest.raises(
            psycopg.errors.RaiseException, match="IMMUTABLE_TOOL_RUN_SNAPSHOT"
        ):
            connection.execute(
                "UPDATE tool_product_runs SET snapshot_json='{}'::jsonb WHERE run_id=%s",
                (first.json()["run_id"],),
            )
    assert run["release_id"] == release.release_id
    assert run["action"]["id"] == "coral-one-shot"
    assert run["capabilities"][0]["id"] == "coral.generate.one-shot"
    assert run["capabilities"][0]["version"] == "1.0.0"
    assert run["service_revisions"] == {"coral": 4}
    assert steps[0][0] == "binding-one-shot" and steps[0][1].startswith("compute-")
    assert count == 1


def test_start_revalidates_input_visibility_quota_and_idempotency(tool_run_system):
    database, repository, _jobs, release, _actor, client = tool_run_system
    assert start(client).status_code == 200

    changed = start(client, protein="OTHER", key="run-key")
    invalid = start(client, protein="x", key="invalid")
    missing = start(client, action="missing", key="missing")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO compute_cpu_limits(user_id,limit_ms) VALUES ('alice',60)"
        )
    quota = start(client, action="coral-pocket", key="quota")
    with database.connection() as connection:
        leaked = connection.execute(
            "SELECT count(*) FROM tool_product_runs WHERE idempotency_key='quota'"
        ).fetchone()[0]
    repository.suspend(release.release_id, "admin")
    revoked = start(client, key="revoked")

    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_ARGUMENTS"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "TOOL_PRODUCT_ACTION_NOT_AVAILABLE"
    assert quota.status_code == 429
    assert quota.json()["detail"]["code"] == "CPU_QUOTA_EXCEEDED"
    assert leaked == 0
    assert revoked.status_code == 404


def test_multiple_actions_are_private_owned_and_have_exact_history(tool_run_system):
    _database, _repository, _jobs, _release, actor, client = tool_run_system
    generated = start(client, key="generate")
    pocket = start(client, action="coral-pocket", key="pocket")
    assert generated.status_code == 200 and pocket.status_code == 200
    assert generated.json()["run_id"] != pocket.json()["run_id"]

    history = client.get("/api/v1/tool-products/coral/runs")
    assert history.status_code == 200
    assert {item["action_id"] for item in history.json()} == {
        "coral-one-shot", "coral-pocket",
    }
    assert "endpoint" not in history.text and "credential" not in history.text

    actor["id"] = "bob"
    run_id = generated.json()["run_id"]
    assert client.get(f"/api/v1/tool-runs/{run_id}").status_code == 404
    assert client.post(f"/api/v1/tool-runs/{run_id}/cancel").status_code == 404
    assert client.post(
        f"/api/v1/tool-runs/{run_id}/agent-handoffs/analyze"
    ).status_code == 404

    actor["id"] = "alice"
    handoff = client.post(
        f"/api/v1/tool-runs/{run_id}/agent-handoffs/analyze"
    )
    assert handoff.status_code == 200
    assert handoff.json() == {
        "run_id": run_id,
        "handoff_id": "analyze",
        "summary": "null",
        "artifacts": [],
    }
    cancelled = client.post(f"/api/v1/tool-runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
