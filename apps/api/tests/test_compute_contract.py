"""Consumer contracts for maintainer-reported computation results."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.contracts.compute import CapabilityVersion, ComputeBudget, ExecutionReport, UsageReport


def test_unknown_usage_is_not_converted_to_zero():
    report = TypeAdapter(ExecutionReport).validate_python({
        "status": "failed", "error": {"code": "OOM", "message": "memory exhausted"},
        "usage": {"gpu_device_ms": 1234, "source": "service_reported"},
    })
    assert report.usage.gpu_device_ms == 1234
    assert report.usage.cpu_core_ms is None
    assert report.usage.peak_memory_bytes is None


@pytest.mark.parametrize("value", [-1, 1.5, True, "100"])
def test_usage_rejects_invalid_units(value):
    with pytest.raises(ValidationError):
        UsageReport(gpu_device_ms=value, source="measured")


def test_pending_has_no_final_usage():
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionReport).validate_python({
            "status": "pending", "job_id": "remote-1", "usage": {"source": "unknown"},
        })


def test_remote_gpu_budget_requires_gpu_usage_without_local_devices():
    with pytest.raises(ValidationError, match="GPU budgets must require gpu_device_ms"):
        CapabilityVersion(id="remote.predict", version="1", input_schema={"type": "object"},
                          gpu_count=0, max_budget=ComputeBudget(gpu_device_ms=60000))
