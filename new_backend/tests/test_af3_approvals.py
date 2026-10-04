import pytest

from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore


def test_rejected_af3_approval_cancels_run_without_creating_a_chat_message(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    project_id = store.project_for(user_id).id
    session_id = store.create_session(user_id, project_id, "AF3 approval").id
    run_id = store.send_message(user_id, session_id, MessageRequest(content="Run AF3")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    approval = store.request_af3_approval(user_id, run_id, "call-1", 40)
    store.set_run_status(run_id, "waiting")

    rejected = store.decide_af3_approval(user_id, run_id, approval.approval_id, "rejected")
    repeated = store.decide_af3_approval(user_id, run_id, approval.approval_id, "rejected")
    with pytest.raises(ValueError, match="conflicts"):
        store.decide_af3_approval(user_id, run_id, approval.approval_id, "approved")

    assert rejected == repeated
    assert rejected.status == "rejected" and rejected.job_id is None
    assert store.run_status_for(user_id, run_id).status == "cancelled"
    assert store.usage_for(user_id).gpu.reserved == 0
    assert [message.role for message in store.messages_for(user_id, session_id)] == ["user"]
    assert [event.type for event in store.events_for(user_id, run_id, None)] == [
        "approval.required", "approval.resolved", "run.cancelled",
    ]


def test_cancelling_a_run_resolves_its_pending_approval(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    project_id = store.project_for(user_id).id
    session_id = store.create_session(user_id, project_id, "AF3 cancellation").id
    run_id = store.send_message(user_id, session_id, MessageRequest(content="Run AF3")).run_id
    assert store.claim_initial_run(run_id, "worker-a")
    approval = store.request_af3_approval(user_id, run_id, "call-1", 40)
    store.cancel_run(user_id, run_id)

    assert store.run_status_for(user_id, run_id).status == "cancelled"
    assert not store.has_pending_approval_for_run(run_id)
    assert [event.type for event in store.events_for(user_id, run_id, None)] == [
        "approval.required", "approval.resolved", "run.cancelled",
    ]
    with pytest.raises(ValueError, match="conflicts"):
        store.decide_af3_approval(user_id, run_id, approval.approval_id, "approved")
