"""Daily activity includes generic device milliseconds and survives pool restart."""

from datetime import UTC, datetime

from compute_support import request

from app.contracts.compute import UsageReport, UsageWindow
from app.domain.persistent_conversation import PersistentConversationStore


def test_daily_activity_combines_actual_generic_usage_and_tokens_without_holds(ledger_system, monkeypatch):
    database, ledger, jobs = ledger_system
    monkeypatch.setattr("app.domain.persistent_conversation._now", lambda: datetime(2030, 1, 2, tzinfo=UTC))
    job = jobs.submit("alice", request(), "activity")
    store = PersistentConversationStore(database)
    store.charge_tokens("alice", 13)
    store.charge_tokens("bob", 900)
    store.reserve_resume_tokens("alice", "pending", 300)
    with database.transaction() as connection:
        ledger.accept_usage(connection, job.id, 1, UsageReport(gpu_device_ms=20000, source="service_reported"), window=UsageWindow(start=datetime(2030, 1, 1, 23, 59, 50, tzinfo=UTC), end=datetime(2030, 1, 2, 0, 0, 10, tzinfo=UTC)), terminal=True)
    activity = store.activity_for("alice", 2)
    assert [(day.date.isoformat(), day.tokens, day.gpu_ms) for day in activity.days] == [
        ("2030-01-01", 0, 10000), ("2030-01-02", 13, 10000),
    ]
    reopened = PersistentConversationStore(database)
    assert reopened.activity_for("alice", 2) == activity
    assert reopened.activity_for("bob", 2).days[-1].tokens == 900
    assert reopened.activity_for("bob", 2).days[-1].gpu_ms == 0
