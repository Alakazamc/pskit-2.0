from datetime import UTC, datetime, timedelta

from app.contracts.conversation import (
    MessageDeltaData,
    MessageDeltaEvent,
    MessageRequest,
    ToolStartedData,
    ToolStartedEvent,
)
from app.domain.persistent_conversation import PersistentConversationStore


def test_only_expired_pi_run_lease_is_recovered(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    path = str(tmp_path / "shared.sqlite3")
    first = PersistentConversationStore(path)
    second = PersistentConversationStore(path)
    user_id = "alice"
    project_id = first.project_for(user_id).id
    session_id = first.sessions_for(user_id, project_id)[0].id
    request = MessageRequest(content="research question", attachments=[], skills=[], resources=[])
    run_id = first.send_message(user_id, session_id, request).run_id

    assert first.claim_initial_run(run_id, "worker-a")
    second.recover_wakeups()
    assert second.run_status_for(user_id, run_id).status == "running"
    assert second.claim_queued_runs("worker-b") == []
    assert first.renew_leases("worker-a", [run_id]) == {run_id}

    now += timedelta(seconds=11)
    second.recover_wakeups(retry_seconds=2)
    second.recover_wakeups()

    assert second.run_status_for(user_id, run_id).status == "queued"
    assert not first.owns_lease(run_id, "worker-a")
    events = second.events_for(user_id, run_id, None)
    assert [event.type for event in events] == ["run.retrying"]
    assert events[0].data.reset_message is True
    assert second.claim_queued_runs("worker-b") == []
    now += timedelta(seconds=2)
    assert [item[0] for item in second.claim_queued_runs("worker-b")] == [run_id]


def test_interrupted_run_after_tool_execution_starts_fails_closed(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    run_id = store.send_message("alice", "session-alice", MessageRequest(content="Analyze")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    store.append_event("alice", run_id, ToolStartedEvent(
        run_id=run_id, data=ToolStartedData(tool_call_id="call-1", tool="external_write"),
    ))
    now += timedelta(seconds=11)
    store.recover_wakeups(retry_seconds=0.01)
    assert store.run_status_for("alice", run_id).status == "failed"
    assert [event.type for event in store.events_for("alice", run_id, None)] == [
        "tool.started", "run.failed",
    ]


def test_partial_text_is_reset_before_safe_first_run_retry(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    run_id = store.send_message("alice", "session-alice", MessageRequest(content="Analyze")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    store.append_event("alice", run_id, MessageDeltaEvent(
        run_id=run_id, data=MessageDeltaData(delta="partial"),
    ))
    now += timedelta(seconds=11)
    store.recover_wakeups(retry_seconds=0.01)
    events = store.events_for("alice", run_id, None)
    assert [event.type for event in events] == ["message.delta", "run.retrying"]
    assert events[-1].data.reset_message is True


def test_pi_turn_commits_checkpoint_and_reply_with_lease_fencing(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    session_id = "session-alice"
    run_id = store.send_message(user_id, session_id, MessageRequest(content="Analyze")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    store.start_pi_turn(run_id, "worker-a")
    assert store.finish_pi_turn(
        user_id, session_id, run_id, "worker-a", "/tmp/first.jsonl", "", waiting=True,
    )
    assert store.run_status_for(user_id, run_id).status == "waiting"
    assert store.session_file_for(user_id, session_id) == "/tmp/first.jsonl"
    assert store.db.execute(
        "SELECT checkpoint_file FROM agent_runs WHERE id=?", (run_id,),
    ).fetchone() == ("/tmp/first.jsonl",)
    assert store.finish_pi_turn(
        user_id, session_id, run_id, "worker-a", "/tmp/late.jsonl", "late", waiting=False,
    ) is False
    assert store.session_file_for(user_id, session_id) == "/tmp/first.jsonl"


def test_completed_pi_turn_persists_reply_once(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    session_id = "session-alice"
    run_id = store.send_message(user_id, session_id, MessageRequest(content="Analyze")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    store.start_pi_turn(run_id, "worker-a")
    assert store.finish_pi_turn(
        user_id, session_id, run_id, "worker-a", "/tmp/final.jsonl", "Result", waiting=False,
    )
    assert store.run_status_for(user_id, run_id).status == "completed"
    assert store.session_file_for(user_id, session_id) == "/tmp/final.jsonl"
    assert store.finish_pi_turn(
        user_id, session_id, run_id, "worker-a", "/tmp/late.jsonl", "late", waiting=False,
    ) is False
    messages = store.messages_for(user_id, session_id)
    assert [message.parts[0].text for message in messages] == ["Analyze", "Result"]
    assert [event.type for event in store.events_for(user_id, run_id, None)] == ["run.completed"]


def test_interrupted_resume_retries_only_when_current_turn_has_no_tool_started(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    run_id = store.send_message(user_id, "session-alice", MessageRequest(content="AF3")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    assert store.start_pi_turn(run_id, "worker-a")
    job = store.create_af3_job(user_id, 10, run_id, "call-1")
    assert store.finish_pi_turn(
        user_id, "session-alice", run_id, "worker-a", "/tmp/checkpoint.jsonl", "", waiting=True,
    )
    store.settle_af3_job(job.id, "completed", 8, [])
    assert [item[0] for item in store.claim_wakeups("worker-b")] == [run_id]
    assert store.start_pi_turn(run_id, "worker-b")

    now += timedelta(seconds=11)
    store.recover_wakeups(retry_seconds=2)
    assert store.run_status_for(user_id, run_id).status == "waiting"
    assert store.session_file_for(user_id, "session-alice") == "/tmp/checkpoint.jsonl"
    assert store.claim_wakeups("worker-c") == []
    now += timedelta(seconds=2)
    assert [item[0] for item in store.claim_wakeups("worker-c")] == [run_id]
    assert store.start_pi_turn(run_id, "worker-c")
    store.append_event(user_id, run_id, ToolStartedEvent(
        run_id=run_id, data=ToolStartedData(tool_call_id="call-2", tool="external_write"),
    ))
    now += timedelta(seconds=11)
    store.recover_wakeups(retry_seconds=2)
    assert store.run_status_for(user_id, run_id).status == "failed"


def test_three_interrupted_resumes_fail_instead_of_waiting_forever(tmp_path, monkeypatch):
    import app.domain.persistent_conversation as persistence

    now = datetime(2026, 10, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(persistence, "_now", lambda: now)
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    run_id = store.send_message("alice", "session-alice", MessageRequest(content="AF3")).run_id
    assert store.claim_initial_run(run_id, "initial")
    assert store.start_pi_turn(run_id, "initial")
    job = store.create_af3_job("alice", 10, run_id, "call-1")
    assert store.finish_pi_turn(
        "alice", "session-alice", run_id, "initial", "/tmp/checkpoint.jsonl", "", waiting=True,
    )
    store.settle_af3_job(job.id, "completed", 8, [])

    for attempt in range(3):
        owner = f"resume-{attempt}"
        assert [item[0] for item in store.claim_wakeups(owner)] == [run_id]
        assert store.start_pi_turn(run_id, owner)
        now += timedelta(seconds=11)
        store.recover_wakeups(retry_seconds=1)
        now += timedelta(seconds=5)

    assert store.run_status_for("alice", run_id).status == "failed"
    assert store.claim_wakeups("late-worker") == []
    assert [event.type for event in store.events_for("alice", run_id, None)].count("run.retrying") == 2
    assert store.events_for("alice", run_id, None)[-1].data.code == "PI_RESUME_FAILED"
