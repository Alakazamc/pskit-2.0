"""An explicit maintainer adapter; import does not run inference or submit a job."""

from pskit_compute import Completed, ComputeBudget, ComputeFailure, ComputeService, UsageReport


def wrap_existing_model(client):
    service = ComputeService("lab-rna", "weights-v1")

    @service.compute_tool(name="predict", version="1", required_usage=["wall_ms", "gpu_device_ms"],
                          gpu_count=1, max_budget=ComputeBudget(gpu_device_ms=120000))
    async def predict(sequence: str):
        result = await client.predict(sequence)
        usage = UsageReport(wall_ms=result.wall_ms, gpu_device_ms=result.gpu_device_ms,
                            peak_gpu_memory_bytes=result.peak_gpu_memory_bytes,
                            source="service_reported")
        if result.error:
            raise ComputeFailure(code="MODEL_FAILED", message=result.error, usage=usage)
        return Completed(result=result.output, usage=usage, artifacts=result.artifacts)

    return service
