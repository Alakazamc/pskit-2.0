"""Synthetic document tools exercise release admission against isolated PostgreSQL."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.internal_compute import router
from app.contracts.compute import ComputeClaimRequest
from app.contracts.conversation import MessageRequest
from app.contracts.tool_products import QualificationReport, ToolProductDraft
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.common import payload_hash
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.leases import ComputeLeases
from app.domain.compute.ledger import ComputeLedger
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.tool_products.repository import ToolProductRepository
from app.domain.tool_products.ui_schema import validate_tool_ui
from app.services.agent import AgentService

INPUT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"], "additionalProperties": False}


def localized(value):
    return {"en": value, "zh-CN": value}


def document_draft(*, revision=1, audience="restricted", allowed=None):
    binding = {
        "binding_id": "echo-binding", "product_action_id": "echo", "service_id": "documents",
        "service_revision": 1, "capability_id": "documents.echo", "capability_version": "7",
        "adapter": "job_mcp", "submit_tool": "echo_document", "status_tool": "document_status",
        "remote_output_schema": {"type": "object"}, "result_schema": deepcopy(INPUT_SCHEMA),
        "result_mapping": {"kind": "json_pointer", "pointer": "/result"},
        "required_usage": ["cpu_core_ms"], "max_budget": {"cpu_core_ms": 40},
    }
    return ToolProductDraft.model_validate({
        "product_id": "product-documents", "slug": "documents", "revision": revision,
        "owner_user_id": "alice", "title": localized("Document tools"),
        "description": localized("Synthetic document processing"),
        "ui_schema": {
            "product": {"slug": "documents", "title": localized("Document tools"),
                        "description": localized("Synthetic document processing")},
            "page": {"layout": "single-column"},
            "sections": [{"id": "document", "title": localized("Document"), "fields": [{
                "id": "text", "component": "textarea", "label": localized("Text"),
                "input_pointer": "/form/text",
            }]}],
            "actions": [{"id": "run-echo", "label": localized("Echo"),
                         "target": {"action_id": "echo"}}],
            "result_views": [{"id": "result", "component": "json-inspector", "source": "/run/result"}],
        },
        "bindings": [binding, {**deepcopy(binding), "binding_id": "prepare-binding",
                               "product_action_id": "pipeline"},
                     {**deepcopy(binding), "binding_id": "render-binding",
                      "product_action_id": "pipeline"}],
        "actions": [{"id": "echo", "label": localized("Echo"), "kind": "capability",
                     "binding_ids": ["echo-binding"], "input_schema": deepcopy(INPUT_SCHEMA)},
                    {"id": "pipeline", "label": localized("Document pipeline"), "kind": "workflow",
                     "binding_ids": ["prepare-binding", "render-binding"],
                     "input_schema": deepcopy(INPUT_SCHEMA)}],
        "visibility": {"audience": audience, "allowed_user_ids": allowed or ["alice"]},
        "acceptance_suite_id": "synthetic-document-suite", "acceptance_suite_revision": 1,
    })


def publish(repository, draft):
    repository.save_draft("alice", draft.product_id, draft, draft.revision - 1)
    report = QualificationReport(
        report_id=f"document-report-{draft.revision}", product_id=draft.product_id,
        product_revision=draft.revision, service_revision=1,
        binding_digest=payload_hash([item.model_dump(mode="json", by_alias=True) for item in draft.bindings]),
        ui_digest=validate_tool_ui(draft.ui_schema, draft.bindings),
        suite_digest=payload_hash({"suite_id": draft.acceptance_suite_id,
                                  "revision": draft.acceptance_suite_revision}),
        status="passed", protocol_passed=True, scientific_passed=True, cases=[],
        qualified_at=datetime.now(UTC),
    )
    repository.record_qualification(report)
    return repository.publish(draft.product_id, draft.revision, report.report_id, "test-publisher")


@pytest.fixture
def document_tools(pg_schema):
    # Import inside the fixture so the first red run identifies the absent service.
    from app.services.compute_tools import AgentComputeTools

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO mcp_service_endpoints "
            "(endpoint_id,uri,transport,credential_ref,network_zone,state,created_by) "
            "VALUES ('documents-endpoint','http://127.0.0.1:9/mcp','streamable_http',"
            "'synthetic-document-key','private','approved','test-publisher')"
        )
        connection.execute(
            "INSERT INTO mcp_service_revisions "
            "(service_id,revision,endpoint_id,manifest_json,manifest_digest,created_by) "
            "VALUES ('documents',1,'documents-endpoint','{}'::jsonb,%s,'test-publisher')",
            ("a" * 64,),
        )
    repository = ToolProductRepository(database)
    release = publish(repository, document_draft())
    jobs = ComputeJobs(database, ComputeLedger(database, cpu_daily_limit_ms=100, gpu_daily_limit_ms=0))
    tools = AgentComputeTools(database, repository, jobs)
    store = PersistentConversationStore(database)
    run = store.send_message("alice", "session-alice", MessageRequest(content="Echo this document"))
    store.set_run_status(run.run_id, "running")
    agent = AgentService(store, None, "http://127.0.0.1:9", 1, 0.01, af3_executor="disabled")
    app = FastAPI()
    app.state.agent_service, app.state.conversations = agent, store
    app.state.compute_jobs, app.state.agent_compute_tools = jobs, tools
    app.state.settings = SimpleNamespace(compute_service_keys_json="{}")
    app.include_router(router)
    try:
        yield SimpleNamespace(database=database, repository=repository, release=release, jobs=jobs,
                              tools=tools, store=store, run_id=run.run_id, agent=agent, client=TestClient(app))
    finally:
        database.close()


def submit(system, definition, *, call="call-echo", arguments=None, **overrides):
    return system.client.post(
        f"/internal/compute/tools/{definition['name']}/jobs",
        headers={"Authorization": f"Bearer {system.agent.tool_token(system.run_id)}"},
        json={"run_id": system.run_id, "tool_call_id": call,
              "release_id": definition["release_id"], "action_id": definition["action_id"],
              "arguments": arguments or {"text": "hello"}, **overrides},
    )


def test_projection_exposes_only_business_schema_and_leaves_workflows_in_tools_ui(document_tools):
    system = document_tools
    definitions = system.tools.for_user("alice")
    assert len(definitions) == 1
    definition = definitions[0]
    assert set(definition) == {"name", "description", "input_schema", "release_id", "action_id"}
    assert definition["input_schema"] == INPUT_SCHEMA
    assert definition["release_id"] == system.release.release_id
    assert definition["action_id"] == "echo"
    assert "credential" not in str(definitions) and "http://" not in str(definitions)
    assert "budget" not in str(definitions) and "capability_version" not in str(definitions)
    assert system.tools.for_user("bob") == []
    assert system.tools.for_user(None) == []


def test_business_submission_pins_release_budget_and_is_durable_per_tool_call(document_tools):
    system = document_tools
    definition = system.tools.for_user("alice")[0]
    response = submit(system, definition)
    repeated = submit(system, definition)
    assert response.status_code == 200
    assert repeated.status_code == 200 and repeated.json()["id"] == response.json()["id"]
    job = response.json()
    assert job["run_id"] == system.run_id and job["tool_call_id"] == "call-echo"
    assert job["capability"]["id"] == "documents.echo" and job["capability"]["version"] == "7"
    assert job["budget"] == {"cpu_core_ms": 40, "gpu_device_ms": 0}
    with system.database.connection() as connection:
        rows = connection.execute("SELECT execution_binding_json FROM compute_job_data").fetchall()
    assert len(rows) == 1
    assert rows[0][0]["endpoint_url"] == "http://127.0.0.1:9/mcp"
    assert rows[0][0]["submit_tool"] == "echo_document"
    assert rows[0][0]["credential_ref"] == "synthetic-document-key"
    assert submit(system, definition, arguments={"text": "changed"}).status_code == 409
    assert submit(system, definition, call="new-call", budget={"cpu_core_ms": 900}).status_code == 422
    assert submit(system, definition, call="new-call", version="9").status_code == 422


def test_submission_rechecks_visibility_release_state_and_exact_tool_identity(document_tools):
    system = document_tools
    old = system.tools.for_user("alice")[0]
    assert submit(system, {**old, "name": "unpublished_tool"}).status_code == 404
    assert submit(system, old, action_id="pipeline").status_code == 404
    system.repository.suspend(system.release.release_id, "test-publisher")
    assert system.tools.for_user("alice") == []
    assert submit(system, old).status_code == 404
    publish(system.repository, document_draft(revision=2, allowed=["bob"]))
    assert system.tools.for_user("alice") == []
    assert submit(system, old).status_code == 404
    bob_definition = system.tools.for_user("bob")[0]
    assert bob_definition["name"] != old["name"]
    assert submit(system, bob_definition).status_code == 404
    with system.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM compute_job_data").fetchone()[0] == 0


def test_submission_rechecks_endpoint_arguments_run_status_and_quota(document_tools):
    system = document_tools
    definition = system.tools.for_user("alice")[0]
    assert submit(system, definition, arguments={"text": ""}).status_code == 422
    system.store.set_run_status(system.run_id, "waiting")
    assert submit(system, definition).status_code == 409
    system.store.set_run_status(system.run_id, "running")
    with system.database.transaction() as connection:
        connection.execute("UPDATE mcp_service_endpoints SET state='suspended'")
    assert submit(system, definition).status_code == 404
    with system.database.transaction() as connection:
        connection.execute("UPDATE mcp_service_endpoints SET state='approved'")
    assert submit(system, definition, call="first").status_code == 200
    assert submit(system, definition, call="second").status_code == 200
    exceeded = submit(system, definition, call="third")
    assert exceeded.status_code == 429 and exceeded.json()["detail"]["code"] == "CPU_QUOTA_EXCEEDED"
    with system.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM compute_job_data").fetchone()[0] == 2


def test_business_submission_requires_run_token_and_preserves_legacy_route(document_tools):
    system = document_tools
    definition = system.tools.for_user("alice")[0]
    body = {"run_id": system.run_id, "tool_call_id": "call-echo", "release_id": definition["release_id"],
            "action_id": definition["action_id"], "arguments": {"text": "hello"}}
    assert system.client.post(f"/internal/compute/tools/{definition['name']}/jobs", json=body).status_code == 401
    response = system.client.post("/internal/compute/jobs", json={
        "run_id": system.run_id, "tool_call_id": "legacy", "capability_id": "missing",
        "version": "1", "arguments": {}, "budget": {},
    }, headers={"Authorization": f"Bearer {system.agent.tool_token(system.run_id)}"})
    assert response.status_code == 404 and response.json()["detail"]["code"] == "CAPABILITY_NOT_FOUND"


def test_prior_tool_call_cannot_bypass_current_publication_authorization(document_tools):
    system = document_tools
    definition = system.tools.for_user("alice")[0]
    assert submit(system, definition).status_code == 200
    system.repository.suspend(system.release.release_id, "test-publisher")
    assert submit(system, definition).status_code == 404
    with system.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM compute_job_data").fetchone()[0] == 1


def test_similar_action_identifiers_have_distinct_provider_safe_names(document_tools):
    import re

    system = document_tools
    payload = document_draft(revision=2).model_dump(mode="json", by_alias=True)
    for action_id in ("echo-alpha", "echo.alpha"):
        payload["bindings"].append({**deepcopy(payload["bindings"][0]),
                                   "binding_id": f"binding-{action_id}", "product_action_id": action_id})
        payload["actions"].append({**deepcopy(payload["actions"][0]), "id": action_id,
                                  "binding_ids": [f"binding-{action_id}"]})
    publish(system.repository, ToolProductDraft.model_validate(payload))
    definitions = system.tools.for_user("alice")
    assert {item["action_id"] for item in definitions} == {"echo", "echo-alpha", "echo.alpha"}
    names = {item["name"] for item in definitions}
    assert len(names) == 3 and all(re.fullmatch(r"[A-Za-z0-9_]{1,64}", name) for name in names)


def test_same_call_cannot_rebind_to_identical_policy_in_different_release(document_tools):
    system = document_tools
    first_definition = system.tools.for_user("alice")[0]
    first = submit(system, first_definition)
    assert first.status_code == 200
    newer = publish(system.repository, document_draft(revision=2))
    next_definition = system.tools.for_user("alice")[0]
    assert next_definition["release_id"] == newer.release_id
    response = submit(system, next_definition)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    with system.database.connection() as connection:
        rows = connection.execute("SELECT execution_binding_json FROM compute_job_data").fetchall()
    assert len(rows) == 1
    assert "release_id" not in rows[0][0] and "action_id" not in rows[0][0]


def test_business_claim_parses_with_preupgrade_strict_receiver(document_tools):
    from test_compute_release_provenance import LegacyExecutionGrant

    system = document_tools
    submitted = submit(system, system.tools.for_user("alice")[0])
    assert submitted.status_code == 200
    grant = ComputeLeases(system.database, system.jobs.ledger).claim(ComputeClaimRequest(
        service_id="documents", worker_id="synthetic-document-worker",
    ))
    assert grant is not None
    old = LegacyExecutionGrant.model_validate_json(grant.model_dump_json())
    assert old.job.id == submitted.json()["id"]
    assert "release_id" not in old.execution_binding.model_dump()
