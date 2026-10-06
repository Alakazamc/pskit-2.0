"""Owned Tool Product runs admitted through the shared compute quota boundary."""

from __future__ import annotations

import json
import uuid
from typing import Any

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.contracts.compute import Completed, ComputeJobRequest, Failed
from app.contracts.tool_products import ToolRunHandoffContext, ToolRunSnapshot
from app.domain.compute.common import admission_lock, payload_hash, utcnow
from app.domain.tool_products.registry import ToolProductNotAvailable


class ToolRunGateway:
    def __init__(self, database, repository, registry, compute_jobs) -> None:
        self.database = database
        self.repository = repository
        self.registry = registry
        self.compute_jobs = compute_jobs

    def _snapshot(self, user_id: str, run_id: str, *, connection) -> ToolRunSnapshot | None:
        row = connection.execute(
            "SELECT run_id,release_id,action_id,user_id,status,progress,result_json,"
            "artifacts_json,usage_json,created_at,updated_at,snapshot_json "
            "FROM tool_product_runs WHERE run_id=%s AND user_id=%s",
            (run_id, user_id),
        ).fetchone()
        if row is None:
            return None
        status = row[4]
        progress = row[5]
        result = row[6]
        artifacts = row[7]
        usage = row[8]
        step = connection.execute(
            "SELECT compute_job_id FROM tool_product_run_steps "
            "WHERE run_id=%s ORDER BY ordinal DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if step and step[0]:
            job = self.compute_jobs.get(user_id, step[0], connection=connection)
            if job is not None:
                status = job.status
                progress = job.progress
                if isinstance(job.report, Completed):
                    result = job.report.result
                    artifacts = [item.model_dump(mode="json") for item in job.report.artifacts]
                    usage = job.report.usage.model_dump(mode="json")
                elif isinstance(job.report, Failed):
                    artifacts = [item.model_dump(mode="json") for item in job.report.artifacts]
                    usage = job.report.usage.model_dump(mode="json")
        return ToolRunSnapshot(
            run_id=row[0],
            product_slug=row[11]["product_slug"],
            release_id=row[1],
            action_id=row[2],
            user_id=row[3],
            status=status,
            progress=progress,
            result=result,
            artifacts=artifacts,
            usage=usage,
            created_at=row[9],
            updated_at=row[10],
        )

    def start(
        self,
        product,
        action_id: str,
        arguments: dict[str, Any],
        user_id: str,
        idempotency_key: str,
    ) -> ToolRunSnapshot:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        action = next((item for item in product.actions if item.id == action_id), None)
        if action is None:
            raise LookupError("TOOL_PRODUCT_ACTION_NOT_AVAILABLE")
        if not Draft202012Validator(action.input_schema).is_valid(arguments):
            raise ValueError("INVALID_ARGUMENTS")
        request_digest = payload_hash({
            "release_id": product.release_id,
            "action_id": action_id,
            "arguments": arguments,
        })
        with self.database.transaction() as connection:
            admission_lock(connection)
            active = connection.execute(
                "SELECT active_release_id FROM tool_products WHERE product_id=%s FOR UPDATE",
                (product.product_id,),
            ).fetchone()
            if active is None or active[0] != product.release_id:
                raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
            prior = connection.execute(
                "SELECT run_id,request_hash FROM tool_product_runs "
                "WHERE user_id=%s AND idempotency_key=%s",
                (user_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[1] != request_digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self._snapshot(user_id, prior[0], connection=connection)
            release = self.repository.release_snapshot(
                product.release_id, connection=connection
            )
            if release is None or release["state"] != "published":
                raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
            draft = release["draft"]
            bindings = {item["binding_id"]: item for item in draft["bindings"]}
            capabilities = []
            resolved = []
            for binding_id in action.binding_ids:
                binding = bindings[binding_id]
                found = self.compute_jobs.catalog.get(
                    user_id,
                    binding["capability_id"],
                    binding["capability_version"],
                    connection=connection,
                )
                if found is None or found[0] != binding["service_id"]:
                    raise LookupError("CAPABILITY_NOT_FOUND")
                service_id, capability = found
                resolved.append((binding, service_id, capability))
                capabilities.append(capability.model_dump(mode="json"))
            run_id = f"tool-run-{uuid.uuid4()}"
            now = utcnow()
            run_snapshot = {
                "product_slug": product.slug,
                "product_id": product.product_id,
                "release_id": product.release_id,
                "action": action.model_dump(mode="json", by_alias=True),
                "capabilities": capabilities,
                "service_revisions": release["service_revisions"],
            }
            connection.execute(
                "INSERT INTO tool_product_runs "
                "(run_id,release_id,action_id,user_id,status,progress,input_json,snapshot_json,"
                "idempotency_key,request_hash,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,'queued',0,%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    product.release_id,
                    action_id,
                    user_id,
                    Jsonb(arguments),
                    Jsonb(run_snapshot),
                    idempotency_key,
                    request_digest,
                    now,
                    now,
                ),
            )
            binding, _service_id, capability = resolved[0]
            compute = self.compute_jobs.submit(
                user_id,
                ComputeJobRequest(
                    capability_id=capability.id,
                    version=capability.version,
                    arguments=arguments,
                    budget=capability.max_budget,
                ),
                f"tool-product:{run_id}:0",
                connection=connection,
            )
            connection.execute(
                "INSERT INTO tool_product_run_steps "
                "(run_id,step_id,ordinal,binding_id,compute_job_id,status,input_json) "
                "VALUES (%s,%s,0,%s,%s,%s,%s)",
                (
                    run_id,
                    f"step-{uuid.uuid4()}",
                    binding["binding_id"],
                    compute.id,
                    compute.status,
                    Jsonb(arguments),
                ),
            )
            return self._snapshot(user_id, run_id, connection=connection)

    def start_by_slug(
        self,
        slug: str,
        action_id: str,
        arguments: dict[str, Any],
        user_id: str,
        idempotency_key: str,
    ) -> ToolRunSnapshot:
        product = self.registry.resolve(slug, user_id)
        return self.start(product, action_id, arguments, user_id, idempotency_key)

    def get(self, user_id: str, run_id: str) -> ToolRunSnapshot | None:
        with self.database.connection() as connection:
            return self._snapshot(user_id, run_id, connection=connection)

    def cancel(self, user_id: str, run_id: str) -> ToolRunSnapshot:
        with self.database.transaction() as connection:
            run = self._snapshot(user_id, run_id, connection=connection)
            if run is None:
                raise LookupError("TOOL_RUN_NOT_FOUND")
            step = connection.execute(
                "SELECT compute_job_id FROM tool_product_run_steps "
                "WHERE run_id=%s ORDER BY ordinal DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if step and step[0]:
                job = self.compute_jobs.cancel(user_id, step[0], connection=connection)
                connection.execute(
                    "UPDATE tool_product_runs SET status=%s,updated_at=%s WHERE run_id=%s",
                    (job.status, utcnow(), run_id),
                )
            return self._snapshot(user_id, run_id, connection=connection)

    def history(self, user_id: str, slug: str, limit: int = 30) -> list[ToolRunSnapshot]:
        if limit < 1 or limit > 50:
            raise ValueError("INVALID_PAGE_LIMIT")
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM tool_product_runs "
                "WHERE user_id=%s AND snapshot_json->>'product_slug'=%s "
                "ORDER BY created_at DESC,run_id DESC LIMIT %s",
                (user_id, slug, limit),
            ).fetchall()
            return [
                snapshot
                for row in rows
                if (snapshot := self._snapshot(user_id, row[0], connection=connection))
            ]

    @staticmethod
    def _pointer(document: Any, pointer: str) -> Any:
        value = document
        if not pointer.startswith("/run/"):
            raise ValueError("INVALID_HANDOFF_POINTER")
        for raw in pointer[1:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise KeyError(key)
        return value

    def handoff(
        self, user_id: str, run_id: str, handoff_id: str
    ) -> ToolRunHandoffContext:
        """Build bounded server-derived context for creation of a new Agent Session."""
        with self.database.connection() as connection:
            run = self._snapshot(user_id, run_id, connection=connection)
            if run is None:
                raise LookupError("TOOL_RUN_NOT_FOUND")
            release = self.repository.release_snapshot(
                run.release_id, connection=connection
            )
            definition = next(
                (
                    item
                    for item in release["draft"].get("handoffs", [])
                    if item["id"] == handoff_id
                ),
                None,
            )
            if definition is None:
                raise LookupError("TOOL_RUN_HANDOFF_NOT_AVAILABLE")
            document = {"run": run.model_dump(mode="json")}
            try:
                summary_value = self._pointer(document, definition["summary_pointer"])
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                raise ValueError("HANDOFF_CONTEXT_UNAVAILABLE") from exc
            summary = (
                summary_value
                if isinstance(summary_value, str)
                else json.dumps(summary_value, ensure_ascii=False, separators=(",", ":"))
            )[:4000]
            selected = []
            for pointer in definition.get("artifact_pointers", []):
                try:
                    value = self._pointer(document, pointer)
                except (IndexError, KeyError, TypeError, ValueError):
                    continue
                values = value if isinstance(value, list) else [value]
                known = {artifact.id: artifact for artifact in run.artifacts}
                for item in values:
                    artifact_id = item.get("id") if isinstance(item, dict) else item
                    if artifact_id in known and known[artifact_id] not in selected:
                        selected.append(known[artifact_id])
            return ToolRunHandoffContext(
                run_id=run_id,
                handoff_id=handoff_id,
                summary=summary,
                artifacts=selected[:20],
            )
