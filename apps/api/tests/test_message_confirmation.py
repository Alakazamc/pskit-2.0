import json

from app.contracts.conversation import MessageRequest, RunRef
from app.domain.persistent_conversation import PersistentConversationStore


def test_accepted_message_exposes_persisted_identity_and_idempotent_replay(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    request = MessageRequest(content="identical question")
    first, created = store.accept_message("alice", "session-alice", request, None, 2, "key-1", "hash")
    assert created
    assert first.user_message_id == store.messages_for("alice", "session-alice")[0].id
    assert store.existing_message_for("alice", "session-alice", "key-1", "hash") == first
    replay, created = store.accept_message("alice", "session-alice", request, None, 2, "key-1", "hash")
    assert not created and replay == first
    second, _ = store.accept_message("alice", "session-alice", request, None, 2, "key-2", "hash")
    assert second.user_message_id != first.user_message_id
    assert second.user_message_id == store.messages_for("alice", "session-alice")[1].id
    assert store.existing_message_for("bob", "session-alice", "key-1", "hash") is None
    store.db.close()


def test_historical_run_without_message_identity_remains_replayable(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    accepted, _ = store.accept_message("alice", "session-alice", MessageRequest(content="question"), None, 2, "key", "hash")
    context = json.loads(store.db.execute("SELECT context_json FROM agent_runs WHERE id=?", (accepted.run_id,)).fetchone()[0])
    context.pop("user_message_id", None)
    with store.db:
        store.db.execute("UPDATE agent_runs SET context_json=? WHERE id=?", (json.dumps(context), accepted.run_id))
    replay = store.existing_message_for("alice", "session-alice", "key", "hash")
    assert replay.run_id == accepted.run_id
    assert replay.user_message_id is None
    assert RunRef(run_id="legacy").user_message_id is None
    store.db.close()
