"""Generic and compatibility AF3 jobs share one model lane and private discovery."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from pskit_compute.af3_entry import Af3ReceiverLane, McpBearerAuth


@pytest.mark.asyncio
async def test_private_mcp_rejects_missing_or_wrong_bearer_credentials():
    app = FastAPI()

    @app.get("/mcp")
    def ready():
        return {"ready": True}

    private = McpBearerAuth(app, "a" * 32)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=private), base_url="http://test") as client:
        assert (await client.get("/mcp")).status_code == 404
        assert (await client.get("/mcp", headers={"Authorization": "Bearer wrong"})).status_code == 404
        assert (await client.get("/mcp", headers={"Authorization": "Bearer " + "a" * 32})).json() == {"ready": True}


@pytest.mark.asyncio
async def test_pending_compatibility_job_prevents_claiming_a_generic_af3_job():
    calls, cleaned = [], []

    class Worker:
        identity = SimpleNamespace(service_id="af3-mcp")

        def __init__(self, name, active):
            self.name = name
            self.journal = SimpleNamespace(recover=lambda: ["unacknowledged"] if active else [])

        async def run_once(self):
            calls.append(self.name)
            return SimpleNamespace(status="pending", job_id="job")

    lane = Af3ReceiverLane(Worker("generic", False), Worker("compatibility", True),
                          generic_cleanup=cleaned.append, compatibility_cleanup=cleaned.append)
    assert (await lane.run_once()).status == "pending"
    assert calls == ["compatibility"]
    assert cleaned == []
