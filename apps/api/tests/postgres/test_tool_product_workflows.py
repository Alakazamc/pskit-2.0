import pytest
from test_tool_product_repository import report_for
from test_tool_product_runs import capability, product_payload, seed_coral_endpoint

from app.contracts.compute import (
    Completed,
    ComputeClaimRequest,
    ComputeResultRequest,
    ExecutionError,
    Failed,
    UsageReport,
    WorkerResources,
)
from app.contracts.tool_products import ToolProductDraft
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.events import ComputeEvents
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.leases import ComputeLeases
from app.domain.compute.ledger import ComputeLedger
from app.domain.tool_products.registry import ToolProductRegistry
from app.domain.tool_products.repository import ToolProductRepository
from app.domain.tool_products.runs import ToolRunGateway


@pytest.fixture
def workflow_system(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    seed_coral_endpoint(database)
    repository = ToolProductRepository(database)
    catalog = ComputeCatalog(database)
    from app.contracts.compute import ComputeServiceManifest

    catalog.register(ComputeServiceManifest(
        service_id="coral",
        model_version="workflow-1",
        capabilities=[
            capability("coral.generate.one-shot"),
            capability("coral.analyze.pocket"),
        ],
    ))
    jobs = ComputeJobs(
        database,
        ComputeLedger(database, cpu_daily_limit_ms=200, gpu_daily_limit_ms=0),
    )
    payload = product_payload(two_actions=True)
    payload["bindings"][0]["product_action_id"] = "coral-workflow"
    payload["bindings"][1]["product_action_id"] = "coral-workflow"
    payload["actions"] = [{
        "id": "coral-workflow",
        "label": {"en": "Workflow", "zh-CN": "工作流"},
        "kind": "workflow",
        "binding_ids": ["binding-one-shot", "binding-pocket"],
        "input_schema": capability("coral.generate.one-shot").input_schema,
    }]
    payload["ui_schema"]["actions"] = [{
        "id": "run-workflow",
        "label": {"en": "Workflow", "zh-CN": "工作流"},
        "kind": "start_run",
        "target": {"action_id": "coral-workflow"},
    }]
    draft = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(payload), 0
    )
    report = report_for(draft)
    repository.record_qualification(report)
    release = repository.publish(draft.product_id, 1, report.report_id, "admin")
    registry = ToolProductRegistry(repository)
    events = ComputeEvents(database)
    gateway = ToolRunGateway(database, repository, registry, jobs, compute_events=events)
    jobs.events = events
    leases = ComputeLeases(database, jobs.ledger, events=events, tool_runs=gateway)
    try:
        yield database, repository, registry, jobs, events, gateway, leases, release
    finally:
        database.close()


def claim(leases, worker):
    return leases.claim(ComputeClaimRequest(
        service_id="coral", worker_id=worker, resources=WorkerResources()
    ))


def complete(leases, grant, report, seq=1):
    return leases.complete("coral", grant.job.id, ComputeResultRequest(
        worker_id=grant.worker_id,
        attempt=grant.attempt,
        fencing_token=grant.fencing_token,
        seq=seq,
        report=report,
        stopped=True,
    ))


def test_completed_step_submits_the_next_step_once_across_restart(workflow_system):
    database, repository, registry, jobs, events, gateway, leases, release = workflow_system
    product = registry.resolve("coral", "alice")
    run = gateway.start(product, "coral-workflow", {"protein": "MKT"}, "alice", "workflow")
    first = claim(leases, "worker-1")
    first_report = Completed(
        result={"protein": "MKT"},
        usage=UsageReport(cpu_core_ms=12, source="service_reported"),
    )
    receipt = complete(leases, first, first_report)

    restarted = ToolRunGateway(database, repository, registry, jobs, compute_events=events)
    leases.tool_runs = restarted
    assert complete(leases, first, first_report) == receipt
    with database.connection() as connection:
        steps = connection.execute(
            "SELECT ordinal,binding_id,compute_job_id FROM tool_product_run_steps "
            "WHERE run_id=%s ORDER BY ordinal",
            (run.run_id,),
        ).fetchall()
    assert [item[:2] for item in steps] == [
        (0, "binding-one-shot"), (1, "binding-pocket"),
    ]
    assert len({item[2] for item in steps}) == 2
    assert restarted.get("alice", run.run_id).status == "queued"

    second = claim(leases, "worker-2")
    complete(leases, second, Completed(
        result={"pockets": [1, 2]},
        usage=UsageReport(cpu_core_ms=8, source="service_reported"),
    ))
    final = restarted.get("alice", run.run_id)
    assert final.status == "completed"
    assert final.result == {"pockets": [1, 2]}
    assert [event.type for event in events.after(run.run_id, "alice", 0, 100)].count(
        "run.completed"
    ) == 1
    assert release.release_id == final.release_id


def test_failed_step_blocks_downstream_execution(workflow_system):
    database, _repository, registry, _jobs, events, gateway, leases, _release = workflow_system
    run = gateway.start(
        registry.resolve("coral", "alice"),
        "coral-workflow",
        {"protein": "MKT"},
        "alice",
        "failed-workflow",
    )
    grant = claim(leases, "worker-fail")
    complete(leases, grant, Failed(
        error=ExecutionError(code="MODEL_FAILED", message="No result"),
        usage=UsageReport(cpu_core_ms=5, source="service_reported"),
    ))

    with database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM tool_product_run_steps WHERE run_id=%s", (run.run_id,)
        ).fetchone()[0] == 1
    assert gateway.get("alice", run.run_id).status == "failed"
    assert "run.failed" in [
        event.type for event in events.after(run.run_id, "alice", 0, 50)
    ]
