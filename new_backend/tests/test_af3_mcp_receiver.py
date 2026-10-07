"""Legacy AF3 HTTP compatibility still uses the shared receiver and durable ACKs."""

import asyncio
import json
import socket

import httpx
import pytest
from test_af3_spool_mcp import INPUT, JOB

from app.contracts.compute import ComputeClaimRequest, Pending
from pskit_compute.af3_compat import Af3ControlClient, Af3McpExecutor
from pskit_compute.af3_spool import Af3Spool, create_mcp
from pskit_compute.http_adapter import parse_report
from pskit_compute.journal import Journal
from pskit_compute.receiver import Receiver


@pytest.mark.asyncio
async def test_af3_pending_and_lost_terminal_ack_use_the_common_receiver(tmp_path):
    claim = {"id": JOB, "attempt": 1, "lease_token": "lease-test",
             "fold_input": INPUT, "estimated_gpu_minutes": 2}
    calls = []
    fail_ack = True

    def backend(request):
        nonlocal fail_ack
        calls.append(request.url.path)
        if request.url.path.endswith("/owned"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=[claim])
        if request.method == "PUT":
            return httpx.Response(200, json={"available": True})
        if request.url.path.endswith("/result"):
            data = json.loads(request.content)
            if fail_ack:
                fail_ack = False
                raise httpx.ReadError("Lost response after database commit")
            return httpx.Response(200, json={"id": JOB, **data})
        return httpx.Response(200, json={"id": JOB, "status": "running"})

    spool = Af3Spool(tmp_path / "jobs")

    class Model:
        async def execute(self, grant, *, context=None):
            return Pending.model_validate(spool.submit(**grant.job.arguments))

        async def poll(self, grant, pending):
            return parse_report(spool.status(pending.job_id), grant.job.capability)

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        control = Af3ControlClient(client, base_url="http://compute", key="private-key",
                                   spool=spool, endpoint_url="http://127.0.0.1:18187/mcp")
        journal = Journal(tmp_path / "receiver.sqlite3")
        worker = Receiver(Model(), control, journal,
                          ComputeClaimRequest(service_id="af3-mcp", worker_id="af3-worker"))
        assert (await worker.run_once()).status == "pending"
        assert len(journal.recover()) == 1
        directory = spool.directory(JOB)
        (directory / "output").mkdir()
        (directory / "output/model.cif").write_text("data_test\n#\n")
        (directory / "outcome.json").write_text(json.dumps({
            "status": "completed", "actual_gpu_minutes": 2, "simulation": False,
        }))
        with pytest.raises(httpx.ReadError):
            await worker.run_once()
        assert journal.recover()[0]["state"] == "outbox"
        assert (directory / "outcome.json").exists()
        assert (await worker.run_once()).status == "acknowledged"
        assert journal.recover() == []
        control.cleanup(JOB)
        assert not directory.exists()
        assert calls.count("/internal/compute/af3/jobs/claim") == 1
        assert calls.count(f"/internal/af3/jobs/{JOB}/result") == 2
        journal.close()


@pytest.mark.asyncio
async def test_af3_uses_real_streamable_http_mcp_with_the_dynamic_executor(tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    spool = Af3Spool(tmp_path / "jobs")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_mcp(spool, port=port).streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        claim = {"id": JOB, "attempt": 1, "lease_token": "lease-test",
                 "fold_input": INPUT, "estimated_gpu_minutes": 2}
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, json=[] if request.url.path.endswith("/owned") else [claim]))
        async with httpx.AsyncClient(transport=transport) as client:
            control = Af3ControlClient(client, base_url="http://compute", key="key", spool=spool,
                                       endpoint_url=f"http://127.0.0.1:{port}/mcp")
            grant = await control.claim(ComputeClaimRequest(service_id="af3-mcp", worker_id="w1"))
        executor = Af3McpExecutor(lambda uri: uri, lambda reference: "", spool=spool)
        pending = await executor.execute(grant)
        assert pending.status == "pending"
        assert pending.job_id == JOB
        directory = spool.directory(JOB)
        modified = (directory / "input.json").stat().st_mtime_ns
        assert (await executor.recover(grant)).status == "pending"
        assert (await executor.recover_detached(grant)).status == "pending"
        assert (directory / "input.json").stat().st_mtime_ns == modified
        (directory / "outcome.json").write_text(json.dumps({
            "status": "completed", "actual_gpu_minutes": 1, "simulation": False,
        }))
        completed = await executor.poll(grant, pending)
        assert completed.status == "completed"
        assert completed.usage.gpu_device_ms == 60000
        assert completed.usage.source == "estimated"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain_coral", [False, True])
async def test_one_process_receives_generic_and_af3_jobs_and_closes_its_local_mcp(tmp_path, monkeypatch, uncertain_coral):
    import uvicorn
    from fastapi import FastAPI, Request

    from scripts.mcp_compute_receiver import argument_parser, run

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("PSKIT_COMPUTE_SERVICE_KEY", "generic-private-key")
    monkeypatch.setenv("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY", "af3-private-key")
    backend = FastAPI()
    calls = []
    claimed = False
    committed = asyncio.Event()
    if uncertain_coral:
        from test_compute_sdk import grant_for

        from pskit_compute import ComputeService

        service = ComputeService("coral-mcp", "v1")

        @service.compute_tool(name="inspect")
        def inspect(sequence: str):
            return {}

        journal = Journal(tmp_path / "coral.sqlite3")
        journal.begin(grant_for(service, {"sequence": "ACG"}))
        journal.close()

    @backend.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
    async def callback(path: str, request: Request):
        nonlocal claimed
        calls.append(path)
        if path == "internal/compute/jobs/claim":
            assert request.headers["x-compute-key"] == "generic-private-key"
            return None
        assert request.headers["x-compute-key"] == "af3-private-key"
        if path.endswith("/owned"):
            return []
        if path.endswith("/claim"):
            if claimed:
                return []
            claimed = True
            return [{"id": JOB, "attempt": 1, "lease_token": "lease-test",
                     "fold_input": INPUT, "estimated_gpu_minutes": 2}]
        if path.endswith("/result"):
            data = await request.json()
            committed.set()
            return {"id": JOB, **data}
        return {"id": JOB, "status": "running"}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    local_sock = socket.socket()
    local_sock.bind(("127.0.0.1", 0))
    mcp_port = local_sock.getsockname()[1]
    local_sock.close()
    server = uvicorn.Server(uvicorn.Config(backend, log_level="critical", access_log=False))
    backend_task = asyncio.create_task(server.serve(sockets=[sock]))
    worker_task = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
            args = argument_parser().parse_args([
                "--backend-url", url, "--service-id", "coral-mcp", "--worker-id", "coral-worker",
                "--journal", str(tmp_path / "coral.sqlite3"), "--poll-seconds", "0.01",
                "--af3-backend-url", url, "--af3-worker-id", "af3-worker",
                "--af3-spool-dir", str(tmp_path / "jobs"), "--af3-mcp-port", str(mcp_port),
            ])
            worker_task = asyncio.create_task(run(args))
            directory = tmp_path / "jobs" / JOB
            while not (directory / "input.json").exists():
                if worker_task.done():
                    await worker_task
                await asyncio.sleep(0.01)
            assert json.loads((directory / "input.json").read_text()) == INPUT
            (directory / "outcome.json").write_text(json.dumps({
                "status": "completed", "actual_gpu_minutes": 1, "simulation": False,
            }))
            await committed.wait()
            while directory.exists():
                await asyncio.sleep(0.01)
        if not uncertain_coral:
            assert "internal/compute/jobs/claim" in calls
        assert "internal/compute/af3/jobs/claim" in calls
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker_task
        server.should_exit = True
        await asyncio.wait_for(backend_task, 5)
        sock.close()
    if uncertain_coral:
        journal = Journal(tmp_path / "coral.sqlite3")
        assert journal.recover()[0]["state"] == "executing"
        journal.close()
    async with httpx.AsyncClient(timeout=1) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(f"http://127.0.0.1:{mcp_port}/mcp")


@pytest.mark.asyncio
@pytest.mark.parametrize("close_stage", [None, "waiting", "pending_ack"])
async def test_lost_result_ack_retries_artifact_upload_against_existing_af3_api(tmp_path, close_stage):
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "backend.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key",
    ), pi_runner=object())

    class LoseAcknowledgement(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.ASGITransport(app=app)
            self.lost = False

        async def handle_async_request(self, request):
            response = await self.transport.handle_async_request(request)
            if request.url.path.endswith("/result") and response.status_code == 200 and not self.lost:
                self.lost = True
                await response.aclose()
                raise httpx.ReadError("Lost ACK after real API commit")
            return response

    async with httpx.AsyncClient(transport=LoseAcknowledgement(), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "mcp@example.org"})).json()
        user_headers = {"Authorization": "Bearer " + login["access_token"]}
        response = await client.post("/api/v1/af3/jobs", headers=user_headers,
                                     json={"fold_input": INPUT, "estimated_gpu_minutes": 2})
        assert response.status_code == 200
        job_id = response.json()["id"]
        spool = Af3Spool(tmp_path / "jobs")
        journal = Journal(tmp_path / "receiver.sqlite3")
        control = Af3ControlClient(client, base_url="http://test", key="compute-key", spool=spool,
                                   endpoint_url="http://127.0.0.1:18187/mcp")

        class Model:
            async def execute(self, grant, *, context=None):
                result = Pending.model_validate(spool.submit(**grant.job.arguments))
                if close_stage == "pending_ack":
                    assert (await client.delete("/api/v1/af3/jobs/" + job_id, headers=user_headers)).status_code == 200
                return result

            async def poll(self, grant, pending):
                return parse_report(spool.status(pending.job_id), grant.job.capability)

            async def cancel(self, grant, pending):
                return False  # AF3 does not advertise physical cancellation.

        worker = Receiver(Model(), control, journal,
                          ComputeClaimRequest(service_id="af3-mcp", worker_id="af3-worker"))
        assert (await worker.run_once()).status == "pending"
        if close_stage == "waiting":
            assert (await client.delete("/api/v1/af3/jobs/" + job_id, headers=user_headers)).status_code == 200
        directory = spool.directory(job_id)
        (directory / "output").mkdir()
        (directory / "output/model.cif").write_text("data_test\n#\n")
        (directory / "outcome.json").write_text(json.dumps({
            "status": "completed", "actual_gpu_minutes": 1, "simulation": False,
        }))
        with pytest.raises(httpx.ReadError):
            await worker.run_once()
        assert journal.recover()[0]["state"] == "outbox"
        state = (await client.get("/api/v1/af3/jobs/" + job_id, headers=user_headers)).json()
        assert state["status"] == ("cancelled" if close_stage else "completed")
        assert state["actual_gpu_minutes"] == 1
        if close_stage:
            assert state["gpu_accounting_status"] == "reconciled"
            assert state["artifacts"] == []
        assert (await worker.run_once()).status == "acknowledged"
        assert journal.recover() == []
        control.cleanup(job_id)
        assert not directory.exists()
        journal.close()
