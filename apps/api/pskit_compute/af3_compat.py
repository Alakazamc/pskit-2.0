"""Translate existing AF3 callbacks at the edge of the common MCP receiver.

The old HTTP surface preserves session wakeups and GPU reservations. Inference
is performed through job-style MCP; this adapter never executes a model.
"""

import hashlib
import math
import shutil
from datetime import UTC, datetime, timedelta

import httpx

from app.contracts.compute import (
    CapabilityVersion,
    ComputeBudget,
    ComputeJob,
    ExecutionBindingSnapshot,
    ExecutionGrant,
    GrantUpdate,
    Pending,
    UsageReceipt,
)
from app.domain.compute.common import payload_hash
from pskit_compute.dynamic_mcp import DynamicMcpExecutor


class Af3McpExecutor(DynamicMcpExecutor):
    def __init__(self, endpoint_resolver, credential_resolver, *, spool=None):
        super().__init__(endpoint_resolver, credential_resolver)
        self.spool = spool

    async def recover(self, grant):
        injected = (grant.execution_binding and grant.execution_binding.submit_job_id_argument == "task_id"
                    and grant.execution_binding.submit_tool == "af3.submit"
                    and grant.execution_binding.status_tool == "af3.status")
        if grant.job.service_id != "af3-mcp" or (
            not injected and grant.job.arguments.get("task_id") != grant.job.id
        ):
            raise ValueError("AF3_RECOVERY_IDENTITY_MISMATCH")
        # The local AF3 MCP service locks and compares input.json before publishing it.
        return await self.execute(grant)

    async def recover_detached(self, grant):
        if (grant.job.service_id != "af3-mcp" or self.spool is None
                or not (self.spool.directory(grant.job.id) / "input.json").exists()):
            raise ValueError("AF3_DETACHED_OUTCOME_UNKNOWN")
        # Observation of a previously published input never authorizes a new submission.
        return await self.poll(grant, Pending(job_id=grant.job.id))


class Af3ControlClient:
    def __init__(self, client, *, base_url, key, spool, endpoint_url,
                 gpu_memory_mb=49140, execution_seconds=86400, mcp_credential_ref=None):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Compute-Key": key}
        self.spool, self.endpoint_url = spool, endpoint_url
        self.gpu_memory_mb, self.execution_seconds = gpu_memory_mb, execution_seconds
        self.acknowledged = set()
        self.mcp_credential_ref = mcp_credential_ref

    async def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        response = await self.client.request(method, self.base_url + path, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    async def claim(self, identity):
        claims = await self.request("GET", "/internal/compute/af3/jobs/owned",
                                    params={"worker_id": identity.worker_id})
        if not claims:
            claims = await self.request("POST", "/internal/compute/af3/jobs/claim", json={
                "worker_id": identity.worker_id, "lease_seconds": 60,
                "resources": {"capabilities": ["af3"], "gpu_count": 1,
                              "gpu_memory_mb": self.gpu_memory_mb},
            })
        if not claims:
            return None
        claim = claims[0]
        capability = CapabilityVersion(
            id="af3.predict", version="1.0.0", input_schema={"type": "object"},
            required_usage=["gpu_device_ms"], accepted_sources=["estimated", "measured"],
            gpu_count=1, cancellation="none", max_execution_seconds=self.execution_seconds,
        )
        now = datetime.now(UTC)
        return ExecutionGrant(
            job=ComputeJob(
                id=claim["id"], user_id="legacy-af3-owner", service_id="af3-mcp",
                capability=capability, status="running", accounting_status="reserved",
                arguments={"task_id": claim["id"], "fold_input": claim["fold_input"]},
                budget=ComputeBudget(gpu_device_ms=claim["estimated_gpu_minutes"] * 60000),
            ),
            worker_id=identity.worker_id, attempt=claim["attempt"],
            fencing_token=claim["lease_token"], stop_at=now + timedelta(seconds=self.execution_seconds),
            lease_expires_at=now + timedelta(seconds=60),
            execution_binding=ExecutionBindingSnapshot(
                adapter="job_mcp", endpoint_url=self.endpoint_url,
                credential_ref=self.mcp_credential_ref,
                submit_tool="af3.submit", status_tool="af3.status",
                remote_output_schema={"type": "object"}, result_mapping={"pointer": "/result"},
            ),
        )

    async def heartbeat(self, grant, payload):
        try:
            await self.request("POST", f"/internal/compute/af3/jobs/{grant.job.id}/heartbeat", json={
                "worker_id": grant.worker_id, "lease_token": grant.fencing_token, "lease_seconds": 60,
            })
            await self.request("POST", f"/internal/compute/af3/jobs/{grant.job.id}/progress", json={
                "worker_id": grant.worker_id, "lease_token": grant.fencing_token,
                "attempt": grant.attempt, "progress": payload.progress,
            })
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 409 or not self.is_late(await self.central_job(grant)):
                raise
            # A closed server job still needs the independently running model's final usage.
            return GrantUpdate(stop_at=grant.stop_at,
                               lease_expires_at=datetime.now(UTC), cancel_requested=True)
        return GrantUpdate(stop_at=grant.stop_at,
                           lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
                           cancel_requested=False)

    async def central_job(self, grant):
        result = await self.request("GET", f"/internal/compute/af3/jobs/{grant.job.id}")
        if result.get("id") != grant.job.id:
            raise ValueError("AF3_CALLBACK_ACK_MISMATCH")
        return result

    @staticmethod
    def is_late(job):
        return job.get("status") in {"cancelled", "failed"} and job.get("gpu_accounting_status") in {
            "pending_reconciliation", "reconciled",
        }

    async def reconcile(self, grant, payload, closed):
        report = payload.report
        minutes = math.ceil(report.usage.gpu_device_ms / 60000)
        result = await self.request("POST", f"/internal/af3/jobs/{grant.job.id}/result", json={
            "status": report.status, "actual_gpu_minutes": minutes,
            "attempt": grant.attempt, "lease_token": grant.fencing_token, "artifacts": [],
        })
        if (result.get("id") != grant.job.id or result.get("status") != closed["status"]
                or result.get("actual_gpu_minutes") != minutes
                or result.get("gpu_accounting_status") != "reconciled"
                or result.get("artifacts", []) != closed.get("artifacts", [])):
            raise ValueError("AF3_RECONCILIATION_ACK_MISMATCH")
        self.acknowledged.add(grant.job.id)
        return self.receipt(grant, payload, result["status"])

    @staticmethod
    def receipt(grant, payload, status):
        return UsageReceipt(receipt_id=f"af3:{grant.job.id}:{payload.seq}", job_id=grant.job.id,
                            accepted_seq=payload.seq, status=status,
                            payload_hash=payload_hash(payload.model_dump(mode="json")))

    async def complete(self, grant, payload):
        report = payload.report
        if isinstance(report, Pending):
            if report.job_id != grant.job.id:
                raise ValueError("AF3_REMOTE_JOB_CONFLICT")
            try:
                acknowledgement = await self.request(
                    "POST", f"/internal/compute/af3/jobs/{grant.job.id}/progress", json={
                        "worker_id": grant.worker_id, "lease_token": grant.fencing_token,
                        "attempt": grant.attempt, "progress": 5,
                    })
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 409 or not self.is_late(await self.central_job(grant)):
                    raise
                # Persist detached waiting; keep journal/spool until final accounting commits.
                return self.receipt(grant, payload, "pending")
            if acknowledgement.get("id") != grant.job.id or acknowledgement.get("status") != "running":
                raise ValueError("AF3_CALLBACK_ACK_MISMATCH")
            status = "pending"
        else:
            state = await self.central_job(grant)
            if self.is_late(state):
                return await self.reconcile(grant, payload, state)
            directory = self.spool.directory(grant.job.id)
            paths = {hashlib.sha256((grant.job.id + "/" + p.relative_to(directory / "output")
                                    .as_posix()).encode()).hexdigest()[:32]: p
                     for p in (directory / "output").rglob("*") if p.is_file()}
            artifacts = []
            for artifact in report.artifacts:
                path = paths.get(artifact.id)
                if path is None or path.is_symlink() or path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("AF3_ARTIFACT_UNAVAILABLE")
                content = path.read_bytes()
                if artifact.sha256 and hashlib.sha256(content).hexdigest() != artifact.sha256:
                    raise ValueError("AF3_ARTIFACT_CHANGED")
                try:
                    await self.request("PUT", f"/internal/af3/jobs/{grant.job.id}/artifacts/{artifact.id}",
                                       params={"name": artifact.name, "kind": artifact.kind,
                                               "attempt": grant.attempt}, content=content,
                                       headers={"X-Compute-Lease": grant.fencing_token,
                                                "Content-Type": "application/octet-stream"})
                except httpx.HTTPStatusError as error:
                    state = await self.central_job(grant)
                    if error.response.status_code == 409 and self.is_late(state):
                        return await self.reconcile(grant, payload, state)
                    raise
                artifacts.append({"id": artifact.id, "name": artifact.name, "kind": artifact.kind})
            minutes = math.ceil(report.usage.gpu_device_ms / 60000)
            result = await self.request("POST", f"/internal/af3/jobs/{grant.job.id}/result", json={
                "status": report.status, "actual_gpu_minutes": minutes,
                "simulation": bool(getattr(report, "result", {}).get("simulation", False)),
                "attempt": grant.attempt, "lease_token": grant.fencing_token,
                "artifacts": artifacts,
            })
            if (result.get("id") != grant.job.id or result.get("status") != report.status
                    or result.get("actual_gpu_minutes") != minutes
                    or {a["id"] for a in result.get("artifacts", [])} != {a["id"] for a in artifacts}):
                raise ValueError("AF3_CALLBACK_ACK_MISMATCH")
            status = report.status
            self.acknowledged.add(grant.job.id)
        # Translate only a successful, matching durable legacy server ACK.
        return self.receipt(grant, payload, status)

    def cleanup(self, job_id):
        if job_id not in self.acknowledged:
            raise ValueError("AF3_NOT_ACKNOWLEDGED")
        shutil.rmtree(self.spool.directory(job_id))
        self.acknowledged.discard(job_id)
