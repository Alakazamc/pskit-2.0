from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "tool_product_smoke.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("tool_product_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_smoke_onboards_runs_recovers_and_suspends_a_product():
    smoke = _load_script()
    calls: list[tuple[str, str]] = []
    snapshots = iter([
        {"run_id": "run-1", "status": "running", "progress": 40, "result": None, "artifacts": [], "usage": None},
        {"run_id": "run-1", "status": "completed", "progress": 100,
         "result": {"generated_count": 1},
         "artifacts": [{"id": "artifact-1", "name": "result.csv", "kind": "text/csv", "available": True, "size": 10, "sha256": "a" * 64}],
         "usage": {"wall_ms": 10, "cpu_core_ms": 4, "gpu_device_ms": 6, "gpu_count": 1, "source": "service_reported"}},
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/mcp-probes"):
            return httpx.Response(200, json={"probe_id": "probe-1", "service_id": "synthetic", "service_revision": 3})
        if path.endswith("/services/synthetic/discoveries"):
            return httpx.Response(200, json={"discovery_id": "discovery-1", "tools": [{"name": "synthetic.run"}]})
        if path.endswith("/tool-products/product-synthetic/draft"):
            return httpx.Response(200, json={"product_id": "product-synthetic", "revision": 1})
        if path.endswith("/tool-products/product-synthetic/qualifications"):
            return httpx.Response(200, json={"report_id": "report-1", "status": "passed", "binding_digest": "b" * 64, "ui_digest": "u" * 64, "suite_digest": "s" * 64})
        if path.endswith("/tool-product-releases/report-1/publish"):
            return httpx.Response(200, json={"release_id": "release-1", "slug": "synthetic", "state": "published"})
        if path.endswith("/tool-products/synthetic/actions/generate/runs"):
            return httpx.Response(200, json={"run_id": "run-1", "status": "queued", "progress": 0})
        if path.endswith("/tool-runs/run-1/events"):
            cursor = request.url.params.get("cursor")
            events = [{"sequence": 1, "type": "run.queued"}] if cursor == "0" else [{"sequence": 2, "type": "run.completed"}]
            return httpx.Response(200, json=events)
        if path.endswith("/tool-runs/run-1"):
            return httpx.Response(200, json=next(snapshots))
        if path.endswith("/tool-products/synthetic") and request.method == "GET":
            return httpx.Response(200, json={"release_id": "release-1", "slug": "synthetic", "state": "published"})
        if path.endswith("/tool-product-releases/release-1/suspend"):
            return httpx.Response(200, json={"release_id": "release-1", "slug": "synthetic", "state": "suspended"})
        return httpx.Response(404, json={"detail": "unexpected"})

    draft = {
        "product_id": "product-synthetic", "slug": "synthetic", "revision": 1,
        "bindings": [{"service_id": "synthetic", "service_revision": 1}],
    }
    suite = {"suite_id": "synthetic-suite", "revision": 1, "cases": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://staging.example") as client:
        result = await smoke.run_smoke(
            client, admin_token="admin", user_token="user", service_id="synthetic",
            probe_uri="https://provider.example/mcp", network_zone="public",
            product_id="product-synthetic", slug="synthetic", action_id="generate",
            draft=draft, suite=suite, arguments={"target": "6FXB"}, poll_seconds=0,
        )

    assert result == {
        "release_id": "release-1", "run_id": "run-1", "result": {"generated_count": 1},
        "artifact_ids": ["artifact-1"], "usage_source": "service_reported",
        "binding_digest": "b" * 64, "ui_digest": "u" * 64, "suite_digest": "s" * 64,
    }
    assert calls.count(("GET", "/api/v1/tool-runs/run-1")) == 2
    assert ("POST", "/api/v1/admin/tool-product-releases/release-1/suspend") in calls


@pytest.mark.asyncio
async def test_smoke_refuses_publication_when_qualification_fails():
    smoke = _load_script()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/mcp-probes"):
            return httpx.Response(200, json={"probe_id": "probe-1", "service_id": "synthetic", "service_revision": 1})
        if request.url.path.endswith("/services/synthetic/discoveries"):
            return httpx.Response(200, json={"discovery_id": "discovery-1", "tools": []})
        if request.url.path.endswith("/draft"):
            return httpx.Response(200, json={"product_id": "product-synthetic", "revision": 1})
        if request.url.path.endswith("/qualifications"):
            return httpx.Response(200, json={"report_id": "report-1", "status": "failed"})
        raise AssertionError("publish must not be called")

    draft = {"product_id": "product-synthetic", "revision": 1, "bindings": [{"service_id": "synthetic", "service_revision": 1}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://staging.example") as client:
        with pytest.raises(RuntimeError, match="QUALIFICATION_FAILED"):
            await smoke.run_smoke(
                client, admin_token="admin", user_token="user", service_id="synthetic",
                probe_uri="https://provider.example/mcp", network_zone="public",
                product_id="product-synthetic", slug="synthetic", action_id="generate",
                draft=draft, suite={"suite_id": "suite", "revision": 1, "cases": []},
                arguments={}, poll_seconds=0,
            )
