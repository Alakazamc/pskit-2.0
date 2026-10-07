"""Run and recover one service worker, retaining receipts until central commit."""

import asyncio
import json
from urllib.parse import quote

import httpx

from app.contracts.compute import (
    ComputeHeartbeatRequest,
    ComputeResultRequest,
    ExecutionGrant,
    GrantUpdate,
    Pending,
    ReceiverOutcome,
    UsageReceipt,
)
from pskit_compute.context import ExecutionContext


class ControlClient:
    def __init__(self, client, *, base_url, service_id, service_key):
        self.client, self.base_url = client, base_url.rstrip("/")
        self.headers = {"X-Compute-Service": service_id, "X-Compute-Key": service_key}

    async def claim(self, identity):
        response = await self.client.post(f"{self.base_url}/internal/compute/jobs/claim",
            json=identity.model_dump(mode="json"), headers=self.headers)
        response.raise_for_status()
        data = response.json()
        return ExecutionGrant.model_validate(data) if data else None

    async def complete(self, grant, payload):
        response = await self.client.post(f"{self.base_url}/internal/compute/jobs/{grant.job.id}/result",
            json=payload.model_dump(mode="json"), headers=self.headers)
        response.raise_for_status()
        return UsageReceipt.model_validate(response.json())

    async def heartbeat(self, grant, payload):
        response = await self.client.post(f"{self.base_url}/internal/compute/jobs/{grant.job.id}/heartbeat",
            json=payload.model_dump(mode="json"), headers=self.headers)
        response.raise_for_status()
        return GrantUpdate.model_validate(response.json())

    def _artifact_request(self, grant, artifact):
        url = (f"{self.base_url}/internal/compute/jobs/{grant.job.id}/artifacts/"
               + quote(artifact.id, safe=""))
        headers = {**self.headers, "X-Compute-Worker": grant.worker_id,
                   "X-Compute-Attempt": str(grant.attempt),
                   "X-Compute-Fence": grant.fencing_token,
                   "X-Compute-Artifact": json.dumps(artifact.model_dump(mode="json"))}
        return url, headers

    async def artifact_uploaded(self, grant, artifact):
        url, headers = self._artifact_request(grant, artifact)
        response = await self.client.get(url, headers=headers)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        if response.json() != artifact.model_dump(mode="json"):
            raise ValueError("ARTIFACT_RECEIPT_MISMATCH")
        return True

    async def upload_artifact(self, grant, artifact, raw):
        url, headers = self._artifact_request(grant, artifact)
        response = await self.client.put(url, headers=headers, content=raw)
        response.raise_for_status()
        if response.json() != artifact.model_dump(mode="json"):
            raise ValueError("ARTIFACT_RECEIPT_MISMATCH")


class Receiver:
    def __init__(self, executor, control, journal, identity, *, heartbeat_seconds=10):
        self.executor, self.control, self.journal = executor, control, journal
        self.identity, self.heartbeat_seconds = identity, heartbeat_seconds

    async def _send(self, grant, payload):
        transfer = getattr(self.executor, "transfer_artifacts", None)
        if transfer is not None:
            await transfer(grant, payload.report, self.control)
        receipt = await self.control.complete(grant, payload)
        self.journal.acknowledge(receipt)
        return ReceiverOutcome(status="pending" if receipt.status == "pending" else "acknowledged",
                               job_id=grant.job.id)

    async def _heartbeat(self, grant, seq, progress):
        self.journal.progress(grant.job.id, seq, progress)
        return await self.control.heartbeat(grant, ComputeHeartbeatRequest(
            worker_id=grant.worker_id, attempt=grant.attempt, fencing_token=grant.fencing_token,
            seq=seq, progress=progress))

    async def run_once(self):
        records = self.journal.recover()
        if records:
            record = records[0]
            grant = record["grant"]
            if record["state"] == "outbox":
                return await self._send(grant, record["payload"])
            if record["state"] == "executing":
                # A process crash says nothing about a detached model. Never blindly resubmit.
                recover = getattr(self.executor, "recover", None)
                if recover is None:
                    return ReceiverOutcome(status="unknown", job_id=grant.job.id)
                # Only a trusted adapter with durable input idempotency may recover submission.
                seq = record["seq"] + 1
                update = await self._heartbeat(grant, seq, record["progress"])
                if update.cancel_requested:
                    recover = getattr(self.executor, "recover_detached", None)
                    if recover is None:
                        return ReceiverOutcome(status="unknown", job_id=grant.job.id)
                report = await recover(grant)
            else:
                seq = record["seq"]+1
                update = await self._heartbeat(grant, seq, record["progress"])
                if update.cancel_requested:
                    await self.executor.cancel(grant, record["pending"])
                report = await self.executor.poll(grant, record["pending"])
                if isinstance(report, Pending):
                    return ReceiverOutcome(status="pending", job_id=grant.job.id)
        else:
            grant = await self.control.claim(self.identity)
            if grant is None:
                return ReceiverOutcome(status="idle")
            self.journal.begin(grant)
            recover = getattr(self.executor, "recover", None)
            if grant.recovered and recover is None:
                return ReceiverOutcome(status="unknown", job_id=grant.job.id)
            seq, cancelled, progress = 0, False, 0
            if grant.recovered:
                seq += 1
                update = await self._heartbeat(grant, seq, progress)
                if update.cancel_requested:
                    recover = getattr(self.executor, "recover_detached", None)
                    if recover is None:
                        return ReceiverOutcome(status="unknown", job_id=grant.job.id)
            def on_progress(value):
                nonlocal progress
                progress = max(progress, value)
            context = ExecutionContext(grant, cancelled=lambda: cancelled, progress=on_progress)
            task = asyncio.create_task(recover(grant) if grant.recovered
                                       else self.executor.execute(grant, context=context))
            try:
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=self.heartbeat_seconds)
                    if not done:
                        seq += 1
                        try:
                            update = await self._heartbeat(grant, seq, progress)
                            cancelled = cancelled or update.cancel_requested
                        except (OSError, RuntimeError, httpx.HTTPError):
                            # Loss of authority requests cooperative cancellation. Do not claim stopped.
                            cancelled = True
                report = await task
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        payload = ComputeResultRequest(worker_id=grant.worker_id, attempt=grant.attempt,
            fencing_token=grant.fencing_token, seq=seq+1, report=report,
            stopped=not isinstance(report, Pending))
        self.journal.record(grant, payload)
        return await self._send(grant, payload)
