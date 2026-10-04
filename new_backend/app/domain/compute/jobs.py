"""Owned durable computation jobs with immutable policies and idempotent admission."""

import json
import uuid

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.contracts.compute import ComputeJob
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.common import admission_lock, payload_hash, utcnow


class ComputeJobs:
    def __init__(self, database, ledger=None):
        self.database = database
        self.catalog = ComputeCatalog(database)
        self.ledger = ledger

    def submit(self, user_id, request, idempotency_key, *, run_id=None, tool_call_id=None):
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        digest = payload_hash({**request.model_dump(mode="json"), "run_id": run_id,
                               "tool_call_id": tool_call_id})
        with self.database.transaction() as connection:
            admission_lock(connection)
            prior = connection.execute(
                "SELECT job_id,request_hash FROM compute_job_data WHERE user_id=%s "
                "AND idempotency_key=%s", (user_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior[1] != digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(user_id, prior[0], connection=connection)
            cleanup = connection.execute("SELECT cleanup_state FROM account_tiers WHERE user_id=%s "
                                         "FOR UPDATE", (user_id,)).fetchone()
            if cleanup and cleanup[0] == "deleting":
                raise ValueError("ACCOUNT_DELETING")
            found = self.catalog.get(user_id, request.capability_id, request.version,
                                     connection=connection)
            if found is None:
                raise LookupError("CAPABILITY_NOT_FOUND")
            service_id, capability = found
            if not Draft202012Validator(capability.input_schema).is_valid(request.arguments):
                raise ValueError("INVALID_ARGUMENTS")
            for metric in ("cpu_core_ms", "gpu_device_ms"):
                if getattr(request.budget, metric) > getattr(capability.max_budget, metric):
                    raise ValueError("CAPABILITY_BUDGET_EXCEEDED")
            if capability.gpu_count and request.budget.gpu_device_ms == 0:
                raise ValueError("GPU_BUDGET_REQUIRED")
            if "cpu_core_ms" in capability.required_usage and request.budget.cpu_core_ms == 0:
                raise ValueError("CPU_BUDGET_REQUIRED")
            if run_id:
                run = connection.execute("SELECT user_id,status FROM agent_runs WHERE id=%s "
                                         "FOR UPDATE", (run_id,)).fetchone()
                if not run or run[0] != user_id or run[1] != "running" or not tool_call_id:
                    raise ValueError("RUN_NOT_ACTIVE")
            job_id = f"compute-{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO agent_jobs (id,user_id,run_id,tool_call_id,status,progress,"
                "estimated_minutes,created_at,input_json,resource_requirements_json) "
                "VALUES (%s,%s,%s,%s,'queued',0,0,%s,%s,%s)",
                (job_id, user_id, run_id, tool_call_id, utcnow().isoformat(),
                 json.dumps(request.arguments), json.dumps({"capability": capability.id,
                                                           "gpu_count": capability.gpu_count})),
            )
            connection.execute(
                "INSERT INTO compute_job_data (job_id,service_id,capability_json,arguments_json,"
                "budget_json,user_id,idempotency_key,request_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (job_id, service_id, Jsonb(capability.model_dump(mode="json")), Jsonb(request.arguments),
                 Jsonb(request.budget.model_dump()), user_id, idempotency_key, digest),
            )
            if self.ledger:
                self.ledger.reserve(connection, user_id, job_id, request.budget, utcnow())
            return self.get(user_id, job_id, connection=connection)

    def get(self, user_id, job_id, *, connection=None):
        if connection is None:
            with self.database.connection() as conn:
                return self.get(user_id, job_id, connection=conn)
        row = connection.execute(
            "SELECT j.id,j.user_id,d.service_id,d.capability_json,d.arguments_json,d.budget_json,"
            "j.status,j.gpu_accounting_status,j.progress,j.run_id,j.tool_call_id,d.report_json "
            "FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
            "WHERE j.id=%s AND j.user_id=%s", (job_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return ComputeJob(**dict(zip(("id", "user_id", "service_id", "capability", "arguments",
                                      "budget", "status", "accounting_status", "progress", "run_id",
                                      "tool_call_id", "report"), row)))

    def cancel(self, user_id, job_id):
        with self.database.transaction() as connection:
            admission_lock(connection)
            job = self.get(user_id, job_id, connection=connection)
            if not job:
                raise LookupError("JOB_NOT_FOUND")
            if job.status == "queued":
                connection.execute("UPDATE agent_jobs SET status='cancelled',"
                                   "gpu_accounting_status='released' WHERE id=%s", (job_id,))
                if self.ledger:
                    self.ledger.release(connection, job_id)
            elif job.status == "running":
                connection.execute("UPDATE agent_jobs SET status='cancelling' WHERE id=%s", (job_id,))
            return self.get(user_id, job_id, connection=connection)
