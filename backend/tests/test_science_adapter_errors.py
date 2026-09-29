import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import mcp
import mcp.client.sse
import mcp.client.streamable_http
import pytest

from app.tasks import worker


def _af3_task(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        pskit_af3_gpu_device="0",
        allowed_af3_gpu_devices=["0"],
        pskit_af3_image="alphafold3:test",
        pskit_af3_timeout_seconds=30,
        data_dir=tmp_path,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "validate_af3_research_context", lambda *_args: None)
    monkeypatch.setattr(worker, "af3_gpu_lock", lambda *_args: nullcontext())
    return SimpleNamespace(
        id=uuid4(),
        input_json={"entities": [{"type": "protein", "sequence": "ACDE"}]},
    )


def test_af3_preflight_failure_does_not_normalize_outputs(monkeypatch, tmp_path):
    task = _af3_task(monkeypatch, tmp_path)
    normalized = []

    def fail_gpu(_device):
        raise worker.Af3GpuMemoryUnavailable("GPU busy")

    monkeypatch.setattr(worker, "require_af3_gpu_memory_available", fail_gpu)
    monkeypatch.setattr(worker, "normalize_af3_output_tree", lambda _path: normalized.append(True))

    with pytest.raises(worker.Af3GpuMemoryUnavailable, match="GPU busy"):
        worker.run_alphafold3_task(None, None, task, tmp_path)
    assert not normalized


def test_af3_normalization_failure_keeps_primary_error(monkeypatch, tmp_path):
    task = _af3_task(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "require_af3_gpu_memory_available", lambda _device: 50000)
    monkeypatch.setattr(worker, "snapshot_active_af3_containers", lambda _image: set())
    monkeypatch.setattr(worker, "legacy_python_executable", lambda: "python")

    def fail_run(*_args, **_kwargs):
        raise worker.Af3ExecutionTimeout("AF3 timed out")

    def fail_normalization(_path):
        raise worker.TaskWorkerError("normalization failed")

    monkeypatch.setattr(worker, "run_command", fail_run)
    monkeypatch.setattr(worker, "normalize_af3_output_tree", fail_normalization)

    with pytest.raises(worker.Af3ExecutionTimeout, match="AF3 timed out"):
        worker.run_alphafold3_task(None, None, task, tmp_path)


class FakeTransport:
    def __init__(self, streams):
        self.streams = streams

    async def __aenter__(self):
        return self.streams

    async def __aexit__(self, *_args):
        return None


class FakeSession:
    def __init__(self, result=None, tools=None):
        self.result = result
        self.tools = tools or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, _name, _payload):
        return self.result


def test_coral_accepts_shared_mcp_structured_list(monkeypatch):
    result = SimpleNamespace(isError=False, structured_content=["ACGU"], content=[])
    session = FakeSession(result=result)
    monkeypatch.setattr(mcp, "ClientSession", lambda *_args: session)
    monkeypatch.setattr(
        mcp.client.sse,
        "sse_client",
        lambda _url: FakeTransport((object(), object())),
    )
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            remote_rna_expert_sse_url="http://example.test/sse",
            task_mcp_timeout_seconds=10,
        ),
    )

    assert asyncio.run(worker.call_remote_rna_expert({"pdb_id": "1ABC", "chain": "A"})) == [
        "ACGU"
    ]


def test_pepccd_preserves_local_tool_schema_error(monkeypatch):
    session = FakeSession(
        tools=[SimpleNamespace(
            name="generate_peptides",
            inputSchema={"properties": {"protein_sequence": {}}},
        )]
    )
    monkeypatch.setattr(mcp, "ClientSession", lambda *_args: session)
    monkeypatch.setattr(
        mcp.client.streamable_http,
        "streamable_http_client",
        lambda _url: FakeTransport((object(), object(), None)),
    )
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            pepccd_mcp_url="http://example.test/mcp",
            pepccd_mcp_tool_name="generate_peptides",
            task_mcp_timeout_seconds=10,
        ),
    )

    with pytest.raises(worker.TaskWorkerError, match="incompatible schema"):
        asyncio.run(worker.call_pepccd_mcp({"protein_sequence": "ACDE"}))
