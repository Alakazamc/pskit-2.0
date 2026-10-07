"""Operator-only recovery of terminal MCP files, without submitting any inference."""

import argparse
import asyncio
import json

from pydantic import TypeAdapter

from app.config import Settings
from app.contracts.compute import ExecutionBindingSnapshot, ExecutionReport, Pending
from app.db.postgres import PostgresDatabase
from app.domain.admin.qualification import McpQualification
from app.domain.admin.roles import AdminStore
from app.domain.admin.service_endpoints import ApprovedEndpointPolicy
from app.domain.compute.artifacts import ComputeArtifacts
from app.domain.compute.common import admission_lock
from app.domain.tool_products.repository import ToolProductRepository
from pskit_compute.dynamic_mcp import DynamicMcpAdapter


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--artifact-tool", default="pskit.artifacts.read")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("INVALID_BACKFILL_LIMIT")
    settings = Settings.from_env()
    database = PostgresDatabase(settings.database_url, schema=settings.database_schema)
    try:
        qualification = McpQualification(database, ToolProductRepository(database),
            ApprovedEndpointPolicy(json.loads(settings.admin_mcp_network_zones_json)),
            credential_refs=json.loads(settings.admin_service_credentials_json))
        with database.connection() as connection:
            rows = connection.execute(
                "SELECT j.id,j.user_id,d.report_json,d.execution_binding_json,b.binding_json "
                "FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
                "JOIN tool_product_run_steps step ON step.compute_job_id=j.id "
                "JOIN tool_product_runs r ON r.run_id=step.run_id "
                "JOIN tool_product_release_capabilities b ON b.release_id=r.release_id "
                "AND b.binding_id=step.binding_id "
                "WHERE d.service_id=%s AND j.status IN ('completed','failed') "
                "ORDER BY j.ordinal DESC LIMIT %s", (args.service_id, args.limit),
            ).fetchall()
        count = 0
        for job_id, owner, raw_report, raw_binding, released_binding in rows:
            report = TypeAdapter(ExecutionReport).validate_python(raw_report)
            if isinstance(report, Pending):
                continue
            # Resolve the exact approved historical endpoint, never provider paths or new URLs.
            from app.contracts.tool_products import CapabilityBinding
            endpoint, _transport, credential = qualification._resolve_binding(
                CapabilityBinding.model_validate(released_binding))
            binding = ExecutionBindingSnapshot.model_validate(raw_binding).model_copy(
                update={"artifact_tool": args.artifact_tool})
            adapter = DynamicMcpAdapter(binding, endpoint_url=endpoint.uri,
                                        credential=credential, timeout_seconds=120)
            for artifact in report.artifacts:
                if not artifact.available:
                    continue
                with database.connection() as connection:
                    exists = connection.execute("SELECT 1 FROM agent_artifact_blobs WHERE id=%s",
                        (ComputeArtifacts.owned_id(job_id, artifact.id),)).fetchone()
                if exists:
                    continue
                from types import SimpleNamespace
                raw = await adapter.read_artifact(SimpleNamespace(job=SimpleNamespace(id=job_id)), artifact)
                with database.transaction() as connection:
                    admission_lock(connection)
                    current = connection.execute(
                        "SELECT j.user_id,d.report_json FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
                        "WHERE j.id=%s AND j.status IN ('completed','failed') FOR UPDATE OF j", (job_id,),
                    ).fetchone()
                    if current != (owner, raw_report):
                        raise ValueError("BACKFILL_JOB_CHANGED")
                    ComputeArtifacts.put(connection, owner, job_id, artifact, raw)
                count += 1
        AdminStore(database).audit(args.actor, "services:write", args.service_id,
            "Restore verified terminal output files without model execution", {}, {"files": count})
        print(json.dumps({"service_id": args.service_id, "jobs_checked": len(rows), "files_restored": count}))
    finally:
        database.close()


if __name__ == "__main__":
    asyncio.run(main())
