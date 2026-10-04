"""Thin SDK: function schema, protocol validation and honest exception usage."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from app.contracts.compute import ComputeBudget, ComputeJob, ExecutionGrant
from pskit_compute import (
    Completed,
    ComputeFailure,
    ComputeService,
    ExecutionContext,
    Failed,
    Pending,
    ProtocolError,
    UsageReport,
)


class SequenceInput(BaseModel):
    sequence: str


def grant_for(service, arguments):
    capability = service.manifest().capabilities[0]
    now = datetime.now(UTC)
    return ExecutionGrant(job=ComputeJob(id="job-1", user_id="alice", service_id="lab",
        capability=capability, arguments=arguments, budget=ComputeBudget(),
        status="running", accounting_status="reserved"), worker_id="worker-1", attempt=1,
        fencing_token="fence-test", stop_at=now+timedelta(seconds=60),
        lease_expires_at=now+timedelta(seconds=30))


@pytest.mark.asyncio
async def test_decorator_excludes_context_and_preserves_service_usage():
    service = ComputeService("lab", "weights-v1")

    @service.compute_tool(name="inspect", required_usage=["wall_ms"])
    def inspect(sequence: str, ctx: ExecutionContext):
        ctx.check_cancelled()
        return Completed(result={"sequence": sequence}, usage=UsageReport(
            wall_ms=234, cpu_core_ms=120, source="service_reported"))

    capability = service.manifest().capabilities[0]
    assert set(capability.input_schema["properties"]) == {"sequence"}
    assert capability.input_schema["required"] == ["sequence"]
    result = await service.execute(grant_for(service, {"sequence": "ACG"}))
    assert result.result == {"sequence": "ACG"}
    assert result.usage.wall_ms == 234
    assert result.usage.gpu_device_ms is None


@pytest.mark.asyncio
async def test_async_pending_and_failed_consumption_are_retained():
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="start")
    async def start(sequence: str):
        return Pending(job_id="external-123")

    assert await service.execute(grant_for(service, {"sequence": "ACG"})) == Pending(job_id="external-123")
    failed = ComputeService("lab", "v1")

    @failed.compute_tool(name="predict", required_usage=["gpu_device_ms"])
    def predict(sequence: str):
        raise ComputeFailure(code="OOM", message="memory exhausted", usage=UsageReport(
            gpu_device_ms=18000, source="service_reported"))

    result = await failed.execute(grant_for(failed, {"sequence": "ACG"}))
    assert isinstance(result, Failed)
    assert result.error.code == "OOM"
    assert result.usage.gpu_device_ms == 18000


@pytest.mark.asyncio
async def test_missing_required_usage_is_rejected_and_unexpected_exception_stays_unknown():
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="predict", required_usage=["wall_ms"])
    def predict(sequence: str):
        if sequence == "crash":
            raise RuntimeError("private service details")
        return Completed(result={}, usage=UsageReport(source="service_reported"))

    with pytest.raises(ProtocolError, match="REQUIRED_USAGE_MISSING"):
        await service.execute(grant_for(service, {"sequence": "ACG"}))
    result = await service.execute(grant_for(service, {"sequence": "crash"}))
    assert result.usage.cpu_core_ms is None
    assert result.usage.gpu_device_ms is None
    assert result.usage.source == "unknown"
    assert "private service details" not in result.error.message


@pytest.mark.asyncio
async def test_typed_model_arguments_reach_function_as_models():
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sample: SequenceInput, samples: list[SequenceInput]):
        return Completed(result={"sequence": sample.sequence,
            "sequences": [item.sequence for item in samples]},
            usage=UsageReport(source="service_reported"))

    report = await service.execute(grant_for(service, {
        "sample": {"sequence": "ACG"}, "samples": [{"sequence": "UGA"}],
    }))
    assert isinstance(report, Completed)
    assert report.result == {"sequence": "ACG", "sequences": ["UGA"]}
