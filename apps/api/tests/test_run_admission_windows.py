from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore


def populate(store, user, count):
    project = store.project_for(user).id
    runs = []
    for index in range(count):
        session = store.create_session(user, project, f"Session {index}").id
        runs.append(store.send_message(user, session, MessageRequest(content="Analyze")).run_id)
    return runs


def test_full_capacity_does_not_scan_queued_candidates(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "test.sqlite3"))
    runs = populate(store, "alice", 120)
    assert store.claim_initial_run(runs[0], "worker", max_active=1)
    queries = []
    store.db.set_trace_callback(queries.append)
    assert store.claim_queued_runs("worker", max_active=1) == []
    selects = [query for query in queries if query.lstrip().startswith("SELECT")]
    assert len(selects) <= 2
    assert not any("context_json" in query for query in selects)


def test_full_user_prefix_does_not_hide_other_user(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "test.sqlite3"))
    alice = populate(store, "alice", 120)
    bob = populate(store, "bob", 1)
    assert store.claim_initial_run(alice[0], "worker", max_user_active=1)
    queries = []
    store.db.set_trace_callback(queries.append)
    claimed = store.claim_queued_runs("worker", max_user_active=1)
    assert [row[0] for row in claimed] == bob
    assert len([query for query in queries if query.lstrip().startswith("SELECT")]) <= 4


def test_zero_limit_prefix_is_bounded_and_eventually_visited(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "test.sqlite3"))
    populate(store, "alice", 250)
    bob = populate(store, "bob", 1)
    store.admin_concurrency_limit_for = lambda user: 0 if user == "alice" else None
    claimed = []
    for _ in range(4):
        batch = store.claim_queued_runs("worker")
        claimed.extend(row[0] for row in batch)
    assert claimed == bob


def test_same_session_and_user_limits_are_preserved_within_batch(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "test.sqlite3"))
    session = store.create_session("alice", store.project_for("alice").id, "Repeated").id
    runs = [
        store.send_message("alice", session, MessageRequest(content="Analyze")).run_id
        for _ in range(4)
    ]
    populate(store, "alice", 4)
    populate(store, "bob", 4)
    claimed = store.claim_queued_runs("worker", max_active=4, max_user_active=2)
    assert len(claimed) == 4
    assert len({row[2] for row in claimed}) == 4
    assert sum(row[1] == "alice" for row in claimed) == 2
    assert sum(row[0] in runs for row in claimed) == 1
