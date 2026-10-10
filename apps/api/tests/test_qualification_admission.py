"""Administrative qualification uses the ordinary durable MCP release ledger."""

import asyncio
from copy import deepcopy

import pytest
from test_admin_tool_product_qualification import completed_execution, qualification_draft
from test_release_mcp_admission import bind, identity

from app.domain.mcp_capacity import McpCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.release_admission import AdmissionClosed
from app.services.qualification_admission import AdmissionMcpQualificationExecutor


class Executor:
    def __init__(self):
        self.invoked = 0
        self.answer = completed_execution()
        self.error = None
        self.started = asyncio.Event()
        self.finish = None

    async def execute(self, _binding, _arguments, _key):
        self.invoked += 1
        self.started.set()
        if self.finish is not None:
            await self.finish.wait()
        if self.error:
            raise self.error
        return deepcopy(self.answer)


@pytest.fixture
def guarded(tmp_path):
    path = str(tmp_path / "qualification.sqlite3")
    calls = McpToolCallStore(path)
    gate = bind(calls)
    shared = McpCapacityStore(path, 1, admission_gate=gate)
    executor = Executor()
    wrapper = AdmissionMcpQualificationExecutor(
        executor, calls, shared, max_calls=1, queue_timeout_seconds=0.01, lease_seconds=30,
    )
    yield wrapper, executor, calls, shared, gate, qualification_draft().bindings[0]
    shared.db.close()
    calls.db.close()


@pytest.mark.asyncio
async def test_hold_denies_qualification_without_executing_or_claiming(guarded):
    wrapper, executor, calls, _shared, gate, binding = guarded
    gate.enter(identity())
    with pytest.raises(AdmissionClosed):
        await wrapper.execute(binding, {"protein": "MKT"}, "new")
    assert executor.invoked == 0
    assert calls.db.execute("SELECT count(*) FROM mcp_tool_calls").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_completed_evidence_is_replayed_during_hold_without_execution(guarded):
    wrapper, executor, calls, _shared, gate, binding = guarded
    first = await wrapper.execute(binding, {"protein": "MKT"}, "old")
    gate.enter(identity())
    assert await wrapper.execute(binding, {"protein": "MKT"}, "old") == first
    assert executor.invoked == 1
    assert calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("completed",)]


@pytest.mark.asyncio
async def test_hold_between_call_and_capacity_claim_releases_only_unstarted_call(guarded, monkeypatch):
    wrapper, executor, calls, shared, gate, binding = guarded
    original = shared.claim

    def claim(seconds):
        gate.enter(identity())
        return original(seconds)

    monkeypatch.setattr(shared, "claim", claim)
    with pytest.raises(AdmissionClosed):
        await wrapper.execute(binding, {"protein": "MKT"}, "race")
    assert executor.invoked == 0
    assert calls.db.execute("SELECT count(*) FROM mcp_tool_calls").fetchone()[0] == 0
    gate.release(identity())
    monkeypatch.setattr(shared, "claim", original)
    await wrapper.execute(binding, {"protein": "MKT"}, "race")
    assert executor.invoked == 1


@pytest.mark.asyncio
async def test_remote_failure_remains_unknown_after_capacity_is_released(guarded):
    wrapper, executor, calls, shared, _gate, binding = guarded
    executor.error = RuntimeError("Synthetic remote outcome unconfirmed")
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await wrapper.execute(binding, {"protein": "MKT"}, "lost")
    assert calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("unknown",)]
    assert shared.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
    with pytest.raises(ValueError, match="QUALIFICATION_OUTCOME_UNKNOWN"):
        await wrapper.execute(binding, {"protein": "MKT"}, "lost")
    assert executor.invoked == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [
    {"status": "pending", "job_id": "synthetic-pending"},
    {"status": "future-terminal"},
    {"status": "completed"},
])
async def test_unacknowledged_report_does_not_disappear_from_activity(guarded, report):
    wrapper, executor, calls, _shared, _gate, binding = guarded
    executor.answer["report"] = report
    await wrapper.execute(binding, {"protein": "MKT"}, "pending")
    assert calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("unknown",)]


@pytest.mark.asyncio
async def test_already_executing_qualification_can_acknowledge_after_hold(guarded):
    wrapper, executor, calls, shared, gate, binding = guarded
    executor.finish = asyncio.Event()
    task = asyncio.create_task(wrapper.execute(binding, {"protein": "MKT"}, "active"))
    await asyncio.wait_for(executor.started.wait(), timeout=2)
    try:
        gate.enter(identity())
        assert calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("running",)]
        assert shared.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 1
        executor.finish.set()
        assert (await task)["report"]["status"] == "completed"
    finally:
        executor.finish.set()
        await asyncio.gather(task, return_exceptions=True)
    assert calls.db.execute("SELECT status FROM mcp_tool_calls").fetchall() == [("completed",)]
    assert shared.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
