"""Real local CPU function -> HTTP control -> PostgreSQL -> replayed committed receipt."""

from time import perf_counter_ns, process_time_ns
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.compute import router as public_router
from app.api.internal_compute import router as worker_router
from app.contracts.compute import ComputeClaimRequest, ComputeJobRequest
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.leases import ComputeLeases
from pskit_compute import Completed, ComputeBudget, ComputeService, UsageReport
from pskit_compute.journal import Journal
from pskit_compute.receiver import ControlClient, Receiver


@pytest.mark.asyncio
async def test_real_cpu_execution_and_lost_ack_never_repeat_inference(ledger_system, tmp_path):
    database, ledger, jobs = ledger_system
    executions = []
    service = ComputeService("cpu-lab", "v1")

    @service.compute_tool(name="inspect", visibility="published", required_usage=["wall_ms", "cpu_core_ms"],
                          max_budget=ComputeBudget(cpu_core_ms=10000))
    def inspect(sequence: str):
        executions.append(sequence)
        wall, cpu = perf_counter_ns(), process_time_ns()
        checksum = sum(index % 7 for index in range(100000))
        return Completed(result={"length": len(sequence), "checksum": checksum}, usage=UsageReport(
            wall_ms=(perf_counter_ns()-wall)//1000000,
            cpu_core_ms=(process_time_ns()-cpu)//1000000, source="measured"))

    ComputeCatalog(database).register(service.manifest())
    job = jobs.submit("alice", ComputeJobRequest(capability_id="cpu-lab.inspect", version="1",
        arguments={"sequence": "ACG"}, budget=ComputeBudget(cpu_core_ms=10000)), "cpu-smoke")
    app = FastAPI()
    app.state.compute_jobs = jobs
    app.state.compute_leases = ComputeLeases(database, ledger)
    app.state.settings = SimpleNamespace(compute_service_keys_json='{"cpu-lab":"test-worker-key"}')
    app.include_router(public_router)
    app.include_router(worker_router)

    class DropAckControl(ControlClient):
        dropped = False
        async def complete(self, grant, payload):
            receipt = await super().complete(grant, payload)
            if not self.dropped:
                self.dropped = True
                raise ConnectionError("commit succeeded but ACK was lost")
            return receipt

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        control = DropAckControl(client, base_url="http://test", service_id="cpu-lab", service_key="test-worker-key")
        identity = ComputeClaimRequest(service_id="cpu-lab", worker_id="cpu-worker")
        path = tmp_path/"receiver.sqlite3"
        journal = Journal(path)
        try:
            with pytest.raises(ConnectionError):
                await Receiver(service, control, journal, identity).run_once()
            assert jobs.get("alice", job.id).status == "completed"
        finally:
            journal.close()
        journal = Journal(path)
        try:
            assert (await Receiver(service, control, journal, identity).run_once()).status == "acknowledged"
            assert journal.recover() == []
        finally:
            journal.close()
    assert executions == ["ACG"]
    settled = jobs.get("alice", job.id)
    assert settled.report.result == {"length": 3, "checksum": 299995}
    assert settled.report.usage.gpu_device_ms is None
    assert ledger.usage_for("alice").cpu.used == settled.report.usage.cpu_core_ms
    assert ledger.usage_for("alice").cpu.reserved == 0
