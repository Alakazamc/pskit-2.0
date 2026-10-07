"""Crash/ACK loss recovery is observable through inference count and local journal."""

import pytest
from test_compute_sdk import grant_for

from app.contracts.compute import ComputeClaimRequest, ComputeResultRequest, UsageReceipt
from app.domain.compute.common import payload_hash
from pskit_compute import Completed, ComputeService, UsageReport
from pskit_compute.journal import Journal
from pskit_compute.receiver import Receiver


class Control:
    def __init__(self, grant):
        self.grant, self.sent, self.fail_ack = grant, [], True

    async def claim(self, request):
        grant, self.grant = self.grant, None
        return grant

    async def complete(self, grant, payload):
        self.sent.append(payload.model_dump(mode="json"))
        if self.fail_ack:
            self.fail_ack = False
            raise ConnectionError("ACK lost after commit")
        return UsageReceipt(receipt_id="committed-1", job_id=grant.job.id,
            accepted_seq=payload.seq, payload_hash=payload_hash(payload.model_dump(mode="json")),
            status=payload.report.status)


@pytest.mark.asyncio
async def test_explicitly_idempotent_model_recovers_an_interrupted_submission(tmp_path):
    from app.contracts.compute import Pending

    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return {}

    grant = grant_for(service, {"sequence": "ACG"})
    recovered = []

    class Idempotent:
        async def recover(self, persisted):
            recovered.append(persisted.job.id)
            return Pending(job_id="existing-model-job")

    control = Control(grant)
    control.fail_ack = False
    async def heartbeat(persisted, payload):
        from types import SimpleNamespace
        return SimpleNamespace(cancel_requested=False)
    control.heartbeat = heartbeat
    journal = Journal(tmp_path / "receiver.sqlite3")
    journal.begin(grant)
    worker = Receiver(Idempotent(), control, journal,
                      ComputeClaimRequest(service_id="lab", worker_id="worker-1"))
    assert (await worker.run_once()).status == "pending"
    assert recovered == [grant.job.id]
    assert journal.recover()[0]["state"] == "waiting"
    journal.close()


@pytest.mark.asyncio
async def test_recovery_does_not_submit_a_model_without_renewed_authority(tmp_path):
    from app.contracts.compute import Pending

    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return {}

    grant = grant_for(service, {"sequence": "ACG"})
    submissions = []

    class Idempotent:
        async def recover(self, persisted):
            submissions.append(persisted.job.id)
            return Pending(job_id="model-job")

    async def rejected_heartbeat(persisted, payload):
        raise ConnectionError("Lease authority unavailable")

    control = Control(grant)
    control.fail_ack = False
    control.heartbeat = rejected_heartbeat
    journal = Journal(tmp_path / "receiver.sqlite3")
    journal.begin(grant)
    worker = Receiver(Idempotent(), control, journal,
                      ComputeClaimRequest(service_id="lab", worker_id="w1"))
    with pytest.raises(ConnectionError):
        await worker.run_once()
    assert submissions == []
    assert journal.recover()[0]["state"] == "executing"
    journal.close()


@pytest.mark.asyncio
async def test_lost_result_ack_replays_without_inference(tmp_path):
    executions = []
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        executions.append(sequence)
        return Completed(result={"sequence": sequence}, usage=UsageReport(source="service_reported"))

    grant = grant_for(service, {"sequence": "ACG"})
    control = Control(grant)
    path = tmp_path/"journal.sqlite3"
    first = Journal(path)
    receiver = Receiver(service, control, first, ComputeClaimRequest(service_id="lab", worker_id="worker-1"))
    with pytest.raises(ConnectionError):
        await receiver.run_once()
    first.close()
    recovered = Journal(path)
    try:
        receiver = Receiver(service, control, recovered, receiver.identity)
        assert (await receiver.run_once()).status == "acknowledged"
        assert executions == ["ACG"]
        assert control.sent[0] == control.sent[1]
        assert recovered.recover() == []
    finally:
        recovered.close()


def test_journal_deletes_only_matching_committed_receipt(tmp_path):
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return {}

    grant = grant_for(service, {"sequence": "ACG"})
    payload = ComputeResultRequest(worker_id="worker-1", attempt=1, fencing_token="fence-test",
        seq=1, stopped=True, report=Completed(result={}, usage=UsageReport(source="service_reported")))
    journal = Journal(tmp_path/"journal.sqlite3")
    try:
        journal.begin(grant)
        journal.record(grant, payload)
        with pytest.raises(ValueError, match="RECEIPT_MISMATCH"):
            journal.acknowledge(UsageReceipt(receipt_id="bad", job_id=grant.job.id,
                accepted_seq=1, payload_hash="wrong", status="completed"))
        assert len(journal.recover()) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_crash_after_started_record_does_not_rerun(tmp_path):
    service = ComputeService("lab", "v1")
    executions = []

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        executions.append(sequence)
        return Completed(result={}, usage=UsageReport(source="service_reported"))

    grant = grant_for(service, {"sequence": "ACG"})
    journal = Journal(tmp_path/"journal.sqlite3")
    try:
        journal.begin(grant)
        receiver = Receiver(service, Control(None), journal,
                            ComputeClaimRequest(service_id="lab", worker_id="worker-1"))
        assert (await receiver.run_once()).status == "unknown"
        assert executions == []
        assert len(journal.recover()) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_pending_waits_for_final_usage_instead_of_resubmitting(tmp_path):
    from types import SimpleNamespace

    from app.contracts.compute import Pending

    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return Pending(job_id="remote-1")

    grant = grant_for(service, {"sequence": "ACG"})
    control = Control(grant)
    control.fail_ack = False
    async def heartbeat(grant, payload):
        return SimpleNamespace(cancel_requested=False)
    control.heartbeat = heartbeat

    class Executor:
        calls = 0
        async def execute(self, grant, context=None):
            self.calls += 1
            return Pending(job_id="remote-1")
        async def poll(self, grant, pending):
            assert pending.job_id == "remote-1"
            return Completed(job_id="remote-1", result={}, usage=UsageReport(
                cpu_core_ms=340, source="service_reported"))

    executor = Executor()
    journal = Journal(tmp_path/"journal.sqlite3")
    try:
        receiver = Receiver(executor, control, journal,
                            ComputeClaimRequest(service_id="lab", worker_id="worker-1"))
        assert (await receiver.run_once()).status == "pending"
        assert len(journal.recover()) == 1
        assert (await receiver.run_once()).status == "acknowledged"
        assert executor.calls == 1
        assert control.sent[-1]["report"]["usage"]["cpu_core_ms"] == 340
        assert journal.recover() == []
    finally:
        journal.close()
