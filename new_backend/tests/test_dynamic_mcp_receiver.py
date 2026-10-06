"""A generic receiver executes only immutable MCP bindings supplied by PSKit."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import types
from test_compute_sdk import grant_for

from app.contracts.compute import (
    Completed,
    ComputeClaimRequest,
    ComputeResultRequest,
    ExecutionBindingSnapshot,
    Pending,
    UsageReceipt,
)
from app.domain.compute.common import payload_hash
from pskit_compute import ComputeService, UsageReport
from pskit_compute.dynamic_mcp import DynamicMcpExecutor
from pskit_compute.journal import Journal
from pskit_compute.receiver import Receiver
from pskit_compute.service import ProtocolError


def binding(adapter="immediate_mcp", **changes):
    values = {
        "adapter": adapter,
        "endpoint_url": "https://mcp.example.org/mcp",
        "credential_ref": "coral-key",
        "submit_tool": "generate",
        "status_tool": "status" if adapter == "job_mcp" else None,
        "cancel_tool": "cancel" if adapter == "job_mcp" else None,
        "remote_output_schema": {"type": "object"},
        "result_mapping": {"kind": "json_pointer", "pointer": "/result"},
    }
    values.update(changes)
    return ExecutionBindingSnapshot.model_validate(values)


def dynamic_grant(*, required_usage=(), output_schema=None, execution_binding=None):
    service = ComputeService("lab", "v1")

    @service.compute_tool(
        name="inspect",
        required_usage=required_usage,
        output_schema=output_schema or {"type": "object"},
    )
    def inspect(sequence: str):
        return {}

    return grant_for(service, {"sequence": "ACG"}).model_copy(
        update={"execution_binding": execution_binding or binding()}
    )


def adapter_for(grant):
    resolved = []
    credentials = []

    def endpoint(value):
        resolved.append(value)
        return value

    def credential(value):
        credentials.append(value)
        return "local-secret"

    adapter = DynamicMcpExecutor.from_grant(grant, endpoint, credential)
    assert resolved == ["https://mcp.example.org/mcp"]
    assert credentials == ["coral-key"]
    assert adapter.remote.bearer_token == "local-secret"
    return adapter


def test_receiver_resolvers_use_exact_endpoint_overrides_and_local_secret_names():
    from scripts.mcp_compute_receiver import configured_resolvers

    endpoint, credential = configured_resolvers({
        "PSKIT_MCP_ENDPOINT_OVERRIDES_JSON": (
            '{"https://mcp.example.org/mcp":"http://10.9.8.2:9100/mcp"}'
        ),
        "PSKIT_MCP_CREDENTIAL_REFS_JSON": '{"coral-key":"CORAL_MCP_TOKEN"}',
        "CORAL_MCP_TOKEN": "secret-value",
    })
    assert endpoint("https://mcp.example.org/mcp") == "http://10.9.8.2:9100/mcp"
    assert endpoint("https://other.example.org/mcp") == "https://other.example.org/mcp"
    assert credential("coral-key") == "secret-value"
    with pytest.raises(ValueError, match="CREDENTIAL_REF_NOT_CONFIGURED"):
        credential("unknown")


def test_dynamic_adapter_uses_the_grants_remaining_execution_window():
    adapter = adapter_for(dynamic_grant())

    assert 30 < adapter.remote.timeout_seconds <= 60


@pytest.mark.asyncio
async def test_immediate_adapter_accepts_envelope_and_maps_bare_result():
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if len(calls) == 1:
                return types.CallToolResult(content=[], structuredContent={
                    "status": "completed",
                    "result": {"mode": "envelope"},
                    "usage": {"cpu_core_ms": 9, "source": "service_reported"},
                })
            return types.CallToolResult(content=[], structuredContent={
                "result": {"mode": "mapped"},
                "usage": {"cpu_core_ms": 10, "source": "service_reported"},
            })

    @asynccontextmanager
    async def session():
        yield Session()

    grant = dynamic_grant(required_usage=["cpu_core_ms"])
    adapter = adapter_for(grant)
    adapter.remote._session = session
    envelope = await adapter.execute(grant)
    mapped = await adapter.execute(grant)
    assert envelope.result == {"mode": "envelope"}
    assert mapped.result == {"mode": "mapped"}
    assert mapped.usage.cpu_core_ms == 10
    assert calls == [("generate", {"sequence": "ACG"})] * 2


@pytest.mark.asyncio
async def test_adapter_enforces_size_schema_usage_and_error_consistency():
    reports = iter([
        types.CallToolResult(content=[], structuredContent={"blob": "x" * (1024 * 1024 + 1)}),
        types.CallToolResult(content=[], structuredContent={"wrong": True}),
        types.CallToolResult(content=[], structuredContent={
            "result": {}, "usage": {"source": "service_reported"},
        }),
        types.CallToolResult(content=[], structuredContent={
            "result": {}, "usage": {"cpu_core_ms": 1, "source": "service_reported"},
        }, isError=True),
    ])

    class Session:
        async def call_tool(self, _name, _arguments):
            return next(reports)

    @asynccontextmanager
    async def session():
        yield Session()

    grant = dynamic_grant(
        required_usage=["cpu_core_ms"],
        execution_binding=binding(remote_output_schema={
            "type": "object", "required": ["result", "usage"]
        }),
    )
    adapter = adapter_for(grant)
    adapter.remote._session = session
    for code in (
        "MCP_REPORT_TOO_LARGE",
        "MCP_REMOTE_OUTPUT_INVALID",
        "REQUIRED_USAGE_MISSING",
        "MCP_REPORT_ERROR_MISMATCH",
    ):
        with pytest.raises(ProtocolError, match=code):
            await adapter.execute(grant)


@pytest.mark.asyncio
async def test_job_adapter_polls_and_cancels_without_accepting_another_remote_job():
    status_job_id = "remote-1"
    cancelled = []

    class Session:
        async def call_tool(self, name, arguments):
            if name == "generate":
                return types.CallToolResult(content=[], structuredContent={
                    "status": "pending", "job_id": "remote-1"
                })
            if name == "status":
                return types.CallToolResult(content=[], structuredContent={
                    "status": "completed", "job_id": status_job_id, "result": {},
                    "usage": {"source": "service_reported"},
                })
            cancelled.append(arguments["job_id"])
            return types.CallToolResult(content=[], structuredContent={"accepted": True})

    @asynccontextmanager
    async def session():
        yield Session()

    grant = dynamic_grant(execution_binding=binding("job_mcp"))
    adapter = adapter_for(grant)
    adapter.remote._session = session
    pending = await adapter.execute(grant)
    assert pending == Pending(job_id="remote-1")
    assert (await adapter.poll(grant, pending)).status == "completed"
    assert await adapter.cancel(grant, pending) is True
    assert cancelled == ["remote-1"]
    status_job_id = "remote-other"
    with pytest.raises(ProtocolError, match="EXTERNAL_JOB_CONFLICT"):
        await adapter.poll(grant, pending)


@pytest.mark.asyncio
async def test_negotiated_mcp_tasks_create_poll_result_and_cancel():
    requests = []

    class Session:
        _server_capabilities = SimpleNamespace(
            tasks=SimpleNamespace(
                requests=SimpleNamespace(tools=SimpleNamespace(call=object())),
                cancel=object(),
            )
        )

        async def send_request(self, request, _result_type, **_kwargs):
            method = request.root.method
            requests.append(method)
            if method == "tools/call":
                assert request.root.params.task is not None
                return SimpleNamespace(task=SimpleNamespace(taskId="task-1"))
            if method == "tasks/get":
                return SimpleNamespace(status="completed", taskId="task-1")
            if method == "tasks/result":
                return types.CallToolResult(content=[], structuredContent={
                    "status": "completed", "job_id": "task-1", "result": {"ok": True},
                    "usage": {"source": "service_reported"},
                })
            return SimpleNamespace()

    @asynccontextmanager
    async def session():
        yield Session()

    grant = dynamic_grant(execution_binding=binding("mcp_tasks"))
    adapter = adapter_for(grant)
    adapter.remote._session = session
    pending = await adapter.execute(grant)
    assert pending == Pending(job_id="task-1")
    completed = await adapter.poll(grant, pending)
    assert completed.result == {"ok": True}
    assert await adapter.cancel(grant, pending) is True
    assert requests == ["tools/call", "tasks/get", "tasks/result", "tasks/cancel"]


class Control:
    def __init__(self, grant=None):
        self.grant = grant
        self.sent = []

    async def claim(self, _identity):
        grant, self.grant = self.grant, None
        return grant

    async def complete(self, grant, payload):
        self.sent.append(payload)
        return UsageReceipt(
            receipt_id="receipt-1",
            job_id=grant.job.id,
            accepted_seq=payload.seq,
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            status=payload.report.status,
        )


@pytest.mark.asyncio
async def test_dynamic_receiver_never_reexecutes_crashed_work_and_replays_outbox(tmp_path):
    grant = dynamic_grant()
    path = tmp_path / "dynamic.sqlite3"
    journal = Journal(path)
    journal.begin(grant)
    journal.close()

    recovered = Journal(path)
    receiver = Receiver(
        DynamicMcpExecutor(lambda value: value, lambda _ref: "secret"),
        Control(),
        recovered,
        ComputeClaimRequest(service_id="lab", worker_id="worker-1"),
    )
    assert (await receiver.run_once()).status == "unknown"
    recovered.close()

    journal = Journal(path)
    payload = ComputeResultRequest(
        worker_id=grant.worker_id,
        attempt=grant.attempt,
        fencing_token=grant.fencing_token,
        seq=1,
        report=Completed(result={}, usage=UsageReport(source="service_reported")),
        stopped=True,
    )
    journal.record(grant, payload)
    control = Control()
    receiver = Receiver(
        DynamicMcpExecutor(lambda value: value, lambda _ref: "secret"),
        control,
        journal,
        ComputeClaimRequest(service_id="lab", worker_id="worker-1"),
    )
    assert (await receiver.run_once()).status == "acknowledged"
    assert control.sent == [payload]
    assert journal.recover() == []
    journal.close()
