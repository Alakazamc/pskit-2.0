"""The real worker HTTP boundary releases verified files only to their owner."""

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from compute_support import request as job_request
from fastapi import FastAPI, Request
from mcp.types import CallToolResult

from app.adapters.live.remote_mcp import RemoteMcp
from app.api import catalog, internal_compute
from app.api.auth import get_current_user
from app.contracts.compute import ComputeClaimRequest, ExecutionBindingSnapshot, WorkerResources
from app.contracts.models import UserIdentity
from app.domain.compute.leases import ComputeLeases
from pskit_compute.dynamic_mcp import DynamicMcpExecutor
from pskit_compute.journal import Journal
from pskit_compute.receiver import ControlClient, Receiver


@pytest.mark.asyncio
async def test_file_ack_and_result_ack_recovery_never_repeat_model_or_file_read(
    ledger_system, tmp_path, monkeypatch,
):
    database, ledger, jobs = ledger_system
    execution_binding = ExecutionBindingSnapshot(
        adapter="immediate_mcp", endpoint_url="http://provider/mcp", submit_tool="generate",
        artifact_tool="read", remote_output_schema={"type": "object"},
        result_mapping={"pointer": "/result"},
    )
    job = jobs.submit("alice", job_request(), "lost-artifact-ack", execution_binding=execution_binding)
    raw = b"sequence\nACGU\n"
    artifact = {"id": "provider-real-output", "name": "candidates.csv", "kind": "data",
                "available": True, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    calls = []

    class ProviderTransport:
        async def call_tool(self, name, arguments):
            calls.append(name)
            data = ({"status": "completed", "result": {},
                     "usage": {"source": "measured", "gpu_device_ms": 100}, "artifacts": [artifact]}
                    if name == "generate" else
                    {"artifact_id": artifact["id"], "offset": 0, "size": len(raw),
                     "sha256": artifact["sha256"], "data_base64": base64.b64encode(raw).decode(),
                     "eof": True})
            return CallToolResult(content=[], structuredContent=data)

    @asynccontextmanager
    async def session(_self):
        yield ProviderTransport()

    monkeypatch.setattr(RemoteMcp, "_session", session)  # External MCP transport seam.
    app = FastAPI()
    app.state.compute_jobs = jobs
    app.state.compute_leases = ComputeLeases(database, ledger)
    app.state.settings = SimpleNamespace(compute_service_keys_json='{"gpu":"key"}')
    app.include_router(internal_compute.router)

    class LostAckControl(ControlClient):
        file_lost, result_lost = False, False

        async def upload_artifact(self, *args):
            await super().upload_artifact(*args)
            if not self.file_lost:
                self.file_lost = True
                raise ConnectionError("file committed but ACK lost")

        async def complete(self, *args):
            receipt = await super().complete(*args)
            if not self.result_lost:
                self.result_lost = True
                raise ConnectionError("result committed but ACK lost")
            return receipt

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        control = LostAckControl(client, base_url="http://test", service_id="gpu", service_key="key")
        executor = DynamicMcpExecutor(lambda endpoint: endpoint, lambda _ref: "")
        identity = ComputeClaimRequest(service_id="gpu", worker_id="worker",
                                       resources=WorkerResources(gpu_uuids=["GPU-1"]))
        path = tmp_path / "journal.sqlite3"
        for lost in ("file committed", "result committed"):
            journal = Journal(path)
            try:
                with pytest.raises(ConnectionError, match=lost):
                    await Receiver(executor, control, journal, identity).run_once()
                assert journal.recover()[0]["state"] == "outbox"
            finally:
                journal.close()
        journal = Journal(path)
        try:
            assert (await Receiver(executor, control, journal, identity).run_once()).status == "acknowledged"
            assert journal.recover() == []
        finally:
            journal.close()
    assert calls == ["generate", "read"]
    owned_id = jobs.get("alice", job.id).report.artifacts[0].id
    assert jobs.artifacts.read("alice", owned_id) == ("candidates.csv", raw)
    assert jobs.artifacts.read("bob", owned_id) is None
    assert jobs.get("alice", job.id).status == "completed"
    assert ledger.usage_for("alice").gpu.used == 100


@pytest.mark.asyncio
async def test_fenced_upload_is_durable_but_download_waits_for_terminal_report(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", job_request(), "artifact-run")
    leases = ComputeLeases(database, ledger)
    grant = leases.claim(ComputeClaimRequest(
        service_id="gpu", worker_id="worker", resources=WorkerResources(gpu_uuids=["GPU-1"])
    ))
    app = FastAPI()
    app.state.settings = SimpleNamespace(compute_service_keys_json='{"gpu":"service-key"}')
    app.state.compute_leases = leases
    app.state.compute_jobs = jobs
    # A legacy shared-table reader must never bypass compute report visibility.
    app.state.af3 = SimpleNamespace(
        artifacts_for=lambda *_args: [], artifact_bytes_for=lambda *_args: ("unsafe.csv", b"unsafe")
    )
    app.include_router(internal_compute.router)
    app.include_router(catalog.router)

    async def identity(request: Request):
        user = request.headers.get("X-Test-User", "alice")
        return UserIdentity(id=user, email=f"{user}@example.org", name=user)

    app.dependency_overrides[get_current_user] = identity
    raw = b"sequence,score\nACGU,0.9\n"
    artifact = {"id": "output-candidates", "name": "candidates.csv", "kind": "data",
                "available": True, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    headers = {
        "X-Compute-Service": "gpu", "X-Compute-Key": "service-key",
        "X-Compute-Worker": grant.worker_id, "X-Compute-Attempt": str(grant.attempt),
        "X-Compute-Fence": grant.fencing_token, "X-Compute-Artifact": json.dumps(artifact),
    }
    upload = f"/internal/compute/jobs/{job.id}/artifacts/{artifact['id']}"
    download = f"/api/v1/artifacts/{artifact['id']}/download"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        stored = await client.put(upload, headers=headers, content=raw)
        assert stored.status_code == 200, stored.text
        assert stored.json() == artifact
        owned_id = jobs.artifacts.owned_id(job.id, artifact["id"])
        download = f"/api/v1/artifacts/{owned_id}/download"
        assert (await client.get(download)).status_code == 404
        assert (await client.put(upload, headers=headers, content=raw)).json() == artifact
        wrong = await client.put(upload, headers={**headers, "X-Compute-Fence": "wrong"}, content=raw)
        assert wrong.status_code == 409
        corrupted = await client.put(upload, headers=headers, content=b"corrupted")
        assert corrupted.status_code == 422
        result = await client.post(f"/internal/compute/jobs/{job.id}/result", headers=headers, json={
            "worker_id": grant.worker_id, "attempt": grant.attempt,
            "fencing_token": grant.fencing_token, "seq": 1, "stopped": True,
            "report": {"status": "completed", "result": {},
                       "usage": {"source": "measured", "gpu_device_ms": 100},
                       "artifacts": [artifact]},
        })
        assert result.status_code == 200, result.text
        owned_id = (await client.get("/api/v1/artifacts")).json()[0]["id"]
        download = f"/api/v1/artifacts/{owned_id}/download"
        fetched = await client.get(download)
        assert fetched.status_code == 200
        assert fetched.content == raw
        assert fetched.headers["x-content-type-options"] == "nosniff"
        assert "candidates.csv" in fetched.headers["content-disposition"]
        assert (await client.get(download, headers={"X-Test-User": "bob"})).status_code == 404
        assert (await client.get(f"/api/v1/artifacts/{owned_id}/preview")).json()["text"] == raw.decode()


@pytest.mark.asyncio
async def test_af3_generic_job_uses_private_mcp_native_spool_and_owned_download(ledger_system, tmp_path, monkeypatch):
    import asyncio
    import socket

    import uvicorn
    from test_af3_spool_mcp import INPUT

    from app.contracts.compute import (
        CapabilityVersion,
        ComputeBudget,
        ComputeJobRequest,
        ComputeServiceManifest,
    )
    from app.domain.compute.catalog import ComputeCatalog
    from pskit_compute.af3_compat import Af3McpExecutor
    from pskit_compute.af3_entry import McpBearerAuth
    from pskit_compute.af3_spool import Af3Spool, create_mcp
    from scripts.af3_receiver import JOB_ID

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    database, ledger, jobs = ledger_system
    ComputeCatalog(database).register(ComputeServiceManifest(service_id="af3-mcp", model_version="1",
        capabilities=[CapabilityVersion(id="af3.predict", version="1", gpu_count=1, input_schema={"type": "object"},
            required_usage=["gpu_device_ms", "gpu_count"], accepted_sources=["estimated"],
            visibility="published", cancellation="none", max_budget=ComputeBudget(gpu_device_ms=60000))]))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    spool = Af3Spool(tmp_path / "jobs")
    token = "private-af3-test-" * 3
    mcp = create_mcp(spool, port=port)
    server = uvicorn.Server(uvicorn.Config(McpBearerAuth(mcp.streamable_http_app(), token),
                                          log_level="critical", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    journal = Journal(tmp_path / "af3.sqlite3")
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        binding = ExecutionBindingSnapshot(adapter="job_mcp", endpoint_url=f"http://127.0.0.1:{port}/mcp",
            credential_ref="af3-key", submit_tool="af3.submit", status_tool="af3.status",
            artifact_tool="pskit.artifacts.read", submit_job_id_argument="task_id",
            remote_output_schema={"type": "object"}, result_mapping={"pointer": "/result"})
        job = jobs.submit("alice", ComputeJobRequest(capability_id="af3.predict", version="1",
            arguments={"fold_input": INPUT}, budget=ComputeBudget(gpu_device_ms=60000)),
            "native-af3", execution_binding=binding)
        app = FastAPI()
        app.state.settings = SimpleNamespace(compute_service_keys_json='{"af3-mcp":"key"}')
        app.state.compute_leases = ComputeLeases(database, ledger)
        app.state.compute_jobs = jobs
        app.include_router(internal_compute.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            worker = Receiver(Af3McpExecutor(lambda uri: uri, lambda ref: token, spool=spool),
                ControlClient(client, base_url="http://test", service_id="af3-mcp", service_key="key"),
                journal, ComputeClaimRequest(service_id="af3-mcp", worker_id="native-af3",
                                             resources=WorkerResources(gpu_uuids=["GPU-af3-test"])))
            assert (await worker.run_once()).status == "pending"
            directory = spool.directory(job.id)
            assert JOB_ID.fullmatch(directory.name)
            assert json.loads((directory / "input.json").read_text()) == INPUT
            (directory / "output").mkdir()
            raw = b"data_structure\n#\n"
            (directory / "output/model.cif").write_bytes(raw)
            (directory / "outcome.json").write_text(json.dumps({
                "status": "completed", "simulation": False, "actual_gpu_minutes": 1}))
            assert (await worker.run_once()).status == "acknowledged"
            final = jobs.get("alice", job.id)
            assert final.status == "completed"
            assert final.report.usage.source == "estimated"
            ref = final.report.artifacts[0]
            assert ref.id.startswith("artifact-compute-")
            assert jobs.artifacts.read("alice", ref.id) == ("model.cif", raw)
            assert jobs.artifacts.read("bob", ref.id) is None
            assert ledger.usage_for("alice").gpu.used == 60000
            assert journal.recover() == []
            bad = jobs.submit("bob", ComputeJobRequest(capability_id="af3.predict", version="1",
                arguments={"fold_input": {**INPUT, "sequences": [{}]}},
                budget=ComputeBudget(gpu_device_ms=60000)), "invalid-af3", execution_binding=binding)
            assert (await worker.run_once()).status == "acknowledged"
            assert jobs.get("bob", bad.id).status == "failed"
            assert ledger.usage_for("bob").gpu.used == 0
            assert journal.recover() == []
            assert not spool.directory(bad.id).exists()
            next_job = jobs.submit("bob", ComputeJobRequest(capability_id="af3.predict", version="1",
                arguments={"fold_input": INPUT}, budget=ComputeBudget(gpu_device_ms=60000)),
                "next-af3", execution_binding=binding)
            assert (await worker.run_once()).status == "pending"
            assert spool.directory(next_job.id).joinpath("input.json").exists()
    finally:
        journal.close()
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        sock.close()
