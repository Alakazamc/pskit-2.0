from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime

import pytest

from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.quota import GpuQuotaExceeded, TokenQuotaExceeded


def _reserved_run(store: PersistentConversationStore, user_id: str, count: int) -> str:
    run_id = f"run-{user_id}"
    with store.db:
        store.db.execute(
            "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) "
            "VALUES (?,?,?,?,?)",
            (run_id, user_id, f"session-{user_id}", "running", datetime.now(UTC).isoformat()),
        )
    store.reserve_resume_tokens(user_id, run_id, count)
    return run_id


def test_model_call_preflight_reserves_budget_and_releases_reported_slack(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "quota.sqlite3"))
    store.set_token_limit("alice", 1000)
    run_id = _reserved_run(store, "alice", 10)

    assert store.reserve_model_call("alice", run_id, "call-1", 200, 300) == 300
    assert store.usage_for("alice").tokens.used == 700
    with pytest.raises(TokenQuotaExceeded):
        store.reserve_model_call("alice", run_id, "call-2", 200, 300)
    store.record_model_attempt("alice", run_id, 50, "completed")
    assert store.usage_for("alice").tokens.used == 50
    assert store.reserve_model_call("alice", run_id, "call-2", 100, 300) == 300
    store.record_model_attempt("alice", run_id, 30, "completed")
    store.settle_current_tokens("alice", run_id)
    assert store.usage_for("alice").tokens.used == 80


def test_unreported_model_call_holds_reserved_budget_across_later_attempts(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "quota.sqlite3"))
    store.set_token_limit("alice", 1000)
    run_id = _reserved_run(store, "alice", 10)

    store.reserve_model_call("alice", run_id, "call-1", 100, 100)
    store.record_unreported_model_call("alice", run_id)
    assert store.usage_for("alice").tokens.used == 300
    store.reserve_model_call("alice", run_id, "call-2", 100, 100)
    store.record_model_attempt("alice", run_id, 30, "completed")
    store.settle_current_tokens("alice", run_id)
    assert store.usage_for("alice").tokens.used == 330
    with pytest.raises(ValueError):
        store.reserve_model_call("alice", run_id, "call-2", 101, 100)


def test_concurrent_token_admission_cannot_spend_the_same_remaining_token(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    stores = [PersistentConversationStore(path), PersistentConversationStore(path)]
    stores[0].set_token_limit("alice", 1)
    barrier = Barrier(2, timeout=2)
    for store in stores:
        original = store.usage_for

        def delayed(user_id: str, *, read=original):
            result = read(user_id)
            barrier.wait()
            return result

        store.usage_for = delayed

    def charge(store: PersistentConversationStore) -> str:
        try:
            store.charge_tokens("alice", 1)
            return "accepted"
        except TokenQuotaExceeded:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(charge, stores))

    assert sorted(results) == ["accepted", "rejected"]
    assert PersistentConversationStore(path).usage_for("alice").tokens.used == 1


def test_concurrent_gpu_reservations_cannot_exceed_daily_limit(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    stores = [PersistentConversationStore(path), PersistentConversationStore(path)]
    barrier = Barrier(2, timeout=2)
    for store in stores:
        original = store.available_gpu

        def delayed(user_id: str, *, read=original):
            result = read(user_id)
            barrier.wait()
            return result

        store.available_gpu = delayed

    def reserve(store: PersistentConversationStore) -> str:
        try:
            store.create_af3_job("alice", 40)
            return "accepted"
        except GpuQuotaExceeded:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, stores))

    assert sorted(results) == ["accepted", "rejected"]
    usage = PersistentConversationStore(path).usage_for("alice").gpu
    assert usage.reserved == 40 and usage.remaining == 20
