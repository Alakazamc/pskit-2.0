"""Transport adapters use only reported metrics; network delay is not GPU time."""

import httpx
import pytest
from test_compute_sdk import grant_for

from pskit_compute import ComputeService, ProtocolError
from pskit_compute.http_adapter import HttpAdapter


@pytest.mark.asyncio
async def test_http_adapter_preserves_reported_usage_and_unknown_gpu():
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return {}

    def handler(request):
        assert request.url.path == "/predict"
        return httpx.Response(200, json={"status": "completed", "result": {"sequence": "ACG"},
                                        "usage": {"wall_ms": 500, "source": "service_reported"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = HttpAdapter(client, submit_url="https://lab.example/predict")
        report = await adapter.execute(grant_for(service, {"sequence": "ACG"}))
        assert report.usage.wall_ms == 500
        assert report.usage.gpu_device_ms is None
        assert report.usage.cpu_core_ms is None


@pytest.mark.asyncio
async def test_http_missing_required_usage_fails_service_validation():
    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect", required_usage=["gpu_device_ms"])
    def inspect(sequence: str):
        return {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={"status": "completed", "result": {}, "usage": {"source": "unknown"}}))) as client:
        with pytest.raises(ProtocolError, match="REQUIRED_USAGE_MISSING"):
            await HttpAdapter(client, submit_url="https://lab.example/predict").execute(
                grant_for(service, {"sequence": "ACG"}))


@pytest.mark.asyncio
async def test_mcp_failed_business_report_keeps_consumed_usage():
    from contextlib import asynccontextmanager

    from mcp.types import CallToolResult

    from pskit_compute.mcp_adapter import McpAdapter

    service = ComputeService("lab", "v1")

    @service.compute_tool(name="inspect")
    def inspect(sequence: str):
        return {}

    class Session:
        async def call_tool(self, name, arguments):
            assert name == "inspect"
            assert arguments == {"sequence": "ACG"}
            return CallToolResult(content=[], isError=True, structuredContent={
                "status": "failed", "error": {"code": "OOM", "message": "memory exhausted"},
                "usage": {"gpu_device_ms": 4200, "source": "service_reported"},
            })

    @asynccontextmanager
    async def session():
        yield Session()

    adapter = McpAdapter(url="https://lab.example/mcp", submit_tool="inspect")
    adapter.remote._session = session  # External JSON-RPC transport; real installed MCP types.
    report = await adapter.execute(grant_for(service, {"sequence": "ACG"}))
    assert report.error.code == "OOM"
    assert report.usage.gpu_device_ms == 4200
