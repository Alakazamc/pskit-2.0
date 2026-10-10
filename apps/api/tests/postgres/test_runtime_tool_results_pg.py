from compute_support import request

from app.contracts.conversation import MessageRequest, ToolStartedData, ToolStartedEvent
from app.domain.persistent_conversation import PersistentConversationStore


def test_dynamic_compute_result_uses_same_recorded_tool_call_name(ledger_system):
    database, _ledger, jobs = ledger_system
    store = PersistentConversationStore(database)
    run = store.send_message("alice", "session-alice", MessageRequest(content="Analyze"))
    store.set_run_status(run.run_id, "running")
    job = jobs.submit("alice", request(), "dynamic-call", run_id=run.run_id, tool_call_id="call-1")
    tool = "compute_release_7_action_3"
    store.append_event(
        "alice",
        run.run_id,
        ToolStartedEvent(
            run_id=run.run_id,
            data=ToolStartedData(tool_call_id="call-1", tool=tool),
        ),
    )
    # A different completed tool must not rename this job's result.
    store.append_event(
        "alice",
        run.run_id,
        ToolStartedEvent(
            run_id=run.run_id,
            data=ToolStartedData(tool_call_id="call-2", tool="other"),
        ),
    )
    with database.transaction() as connection:
        connection.execute("UPDATE agent_jobs SET status='completed' WHERE id=%s", (job.id,))
        connection.execute(
            "UPDATE compute_job_data SET report_json='{}' WHERE job_id=%s", (job.id,)
        )
    results = store._compute_tool_results_for_run("alice", run.run_id)
    assert len(results) == 1
    assert results[0].tool_call_id == "call-1"
    assert results[0].tool == tool
    assert store._compute_tool_results_for_run("bob", run.run_id) == []
