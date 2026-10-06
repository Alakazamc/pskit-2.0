# ruff: noqa: F401, F811
import json

import pytest
from test_tool_product_runs import capability, start, tool_run_system

from app.config import Settings
from app.contracts.catalog import ArtifactRef
from app.contracts.compute import (
    Completed,
    ComputeClaimRequest,
    ComputeHeartbeatRequest,
    ComputeResultRequest,
    ComputeServiceManifest,
    ExecutionError,
    Failed,
    UsageReport,
    WorkerResources,
)
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.events import ComputeEvents
from app.domain.compute.leases import ComputeLeases
from app.main import create_app


def claim(leases, worker="worker-a"):
    return leases.claim(ComputeClaimRequest(
        service_id="coral", worker_id=worker, resources=WorkerResources()
    ))


def result(grant, report, *, seq=2):
    return ComputeResultRequest(
        worker_id=grant.worker_id,
        attempt=grant.attempt,
        fencing_token=grant.fencing_token,
        seq=seq,
        report=report,
        stopped=True,
    )


def wire_events(database, jobs, client):
    events = ComputeEvents(database)
    gateway = client.app.state.tool_run_gateway
    gateway.compute_events = events
    jobs.events = events
    leases = ComputeLeases(database, jobs.ledger, events=events, tool_runs=gateway)
    return gateway, events, leases


def test_progress_usage_artifact_and_terminal_events_are_recoverable(tool_run_system):
    database, _repository, jobs, _release, _actor, client = tool_run_system
    gateway, events, leases = wire_events(database, jobs, client)
    created = start(client, key="event-run").json()
    grant = claim(leases)
    heartbeat = ComputeHeartbeatRequest(
        worker_id=grant.worker_id,
        attempt=grant.attempt,
        fencing_token=grant.fencing_token,
        seq=1,
        progress=35,
        usage=UsageReport(cpu_core_ms=10, wall_ms=12, source="service_reported"),
    )
    leases.heartbeat("coral", grant.job.id, heartbeat)
    report = Completed(
        result={"candidates": ["AUGC"]},
        usage=UsageReport(cpu_core_ms=20, wall_ms=25, source="service_reported"),
        artifacts=[ArtifactRef(
            id="artifact-1", name="candidates.csv", kind="csv", available=True,
            size=12, sha256="a" * 64,
        )],
    )
    receipt = leases.complete("coral", grant.job.id, result(grant, report))
    repeated = leases.complete("coral", grant.job.id, result(grant, report))

    recorded = events.after(created["run_id"], "alice", cursor=0, limit=50)
    types = [event.type for event in recorded]
    assert receipt == repeated
    assert types == [
        "run.queued", "run.started", "stage.started", "stage.progress",
        "usage.updated", "artifact.created", "usage.updated", "stage.completed",
        "run.completed",
    ]
    assert [event.sequence for event in recorded] == list(range(1, len(recorded) + 1))
    assert events.after(created["run_id"], "alice", cursor=4, limit=2) == recorded[4:6]
    assert events.after(created["run_id"], "bob", cursor=0, limit=50) == []
    recovered = client.get(
        f"/api/v1/tool-runs/{created['run_id']}/events",
        params={"cursor": 4, "limit": 2},
    )
    assert recovered.status_code == 200
    assert [item["sequence"] for item in recovered.json()] == [5, 6]
    streamed = client.get(
        f"/api/v1/tool-runs/{created['run_id']}/events",
        params={"cursor": 4, "limit": 2},
        headers={"Accept": "text/event-stream"},
    )
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "id: 5\n" in streamed.text
    assert "event: usage.updated\n" in streamed.text
    projected = gateway.get("alice", created["run_id"])
    assert projected.status == "completed"
    assert projected.progress == 100
    assert projected.result == {"candidates": ["AUGC"]}
    assert projected.usage.cpu_core_ms == 20
    assert projected.artifacts[0].id == "artifact-1"


def test_cancellation_stays_active_until_worker_confirms_stop(tool_run_system):
    database, _repository, jobs, _release, _actor, client = tool_run_system
    gateway, events, leases = wire_events(database, jobs, client)
    run = start(client, key="cancel-run").json()
    grant = claim(leases)
    cancelling = gateway.cancel("alice", run["run_id"])

    assert cancelling.status == "cancelling"
    assert jobs.ledger.usage_for("alice").cpu.reserved == 40
    failed = Failed(
        error=ExecutionError(code="CANCELLED", message="Worker stopped"),
        usage=UsageReport(cpu_core_ms=7, source="service_reported"),
    )
    leases.complete("coral", grant.job.id, result(grant, failed, seq=1))

    terminal = gateway.get("alice", run["run_id"])
    assert terminal.status == "cancelled"
    assert jobs.ledger.usage_for("alice").cpu.reserved == 0
    assert [event.type for event in events.after(run["run_id"], "alice", 0, 50)][-2:] == [
        "stage.completed", "run.cancelled",
    ]


def test_event_cursor_is_bounded_and_rejects_invalid_ranges(tool_run_system):
    database, _repository, jobs, _release, _actor, client = tool_run_system
    _gateway, events, _leases = wire_events(database, jobs, client)
    run_id = start(client, key="bounds").json()["run_id"]
    with pytest.raises(ValueError, match="INVALID_EVENT_CURSOR"):
        events.after(run_id, "alice", -1, 10)
    with pytest.raises(ValueError, match="INVALID_PAGE_LIMIT"):
        events.after(run_id, "alice", 0, 101)


def test_live_app_wires_one_event_stream_through_jobs_leases_and_gateway(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    manifest = ComputeServiceManifest(
        service_id="coral",
        model_version="test",
        capabilities=[capability("coral.generate.one-shot")],
    )
    app = create_app(
        Settings(
            mode="live",
            agent_runtime="pi",
            database_url=dsn,
            database_schema=schema,
            supabase_url="https://identity.invalid",
            supabase_publishable_key="test-only",
            model_gateway_base_url="https://gateway.invalid/v1",
            model_gateway_model="test-model",
            model_gateway_api_key="test-only",
            mcp_executor="disabled",
            af3_executor="disabled",
            compute_enabled=True,
            compute_services_json=json.dumps([manifest.model_dump(mode="json")]),
            auth_csrf_secret="test-only-csrf-secret-at-least-32-characters",
        ),
        pi_runner=object(),
    )
    try:
        assert app.state.compute_jobs.events is app.state.compute_events
        assert app.state.compute_leases.events is app.state.compute_events
        assert app.state.compute_leases.tool_runs is app.state.tool_run_gateway
        assert app.state.tool_run_gateway.compute_events is app.state.compute_events
    finally:
        app.state.database.close()
