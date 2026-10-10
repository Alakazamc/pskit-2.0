from datetime import UTC, datetime

import app.domain.persistent_conversation as persistence
from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore


def test_run_token_adjustment_stays_in_reservation_month_after_midnight(monkeypatch, tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    session_id = store.create_session(user_id, store.project_for(user_id).id, "Token settlement").id
    monkeypatch.setattr(persistence, "_now", lambda: datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    accepted = store.accept_message(
        user_id, session_id, MessageRequest(content="Analyze"), store,
        8, None, "request-hash",
    )
    assert accepted is not None
    run_id = accepted[0].run_id

    monkeypatch.setattr(persistence, "_now", lambda: datetime(2026, 10, 1, 0, 1, tzinfo=UTC))
    store.adjust_tokens(user_id, -3, run_id=run_id)
    store.adjust_tokens(user_id, 4, run_id=run_id)

    rows = store.db.execute(
        "SELECT period,used FROM agent_token_usage WHERE user_id=? ORDER BY period", (user_id,),
    ).fetchall()
    entries = [item for item in store.usage_entries_for(user_id) if item.run_id == run_id]
    assert rows == [("2026-09", 9)]
    assert [(item.period, item.amount) for item in entries] == [
        ("2026-09", 8), ("2026-09", -3), ("2026-09", 4),
    ]
    assert store.usage_for(user_id).tokens.used == 0


def test_background_resume_uses_current_month_quota_and_own_settlement_period(monkeypatch, tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    user_id = "alice"
    session_id = store.create_session(user_id, store.project_for(user_id).id, "Resume settlement").id
    monkeypatch.setattr(persistence, "_now", lambda: datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    accepted = store.accept_message(
        user_id, session_id, MessageRequest(content="Analyze"), store,
        8, None, "request-hash",
    )
    assert accepted is not None
    run_id = accepted[0].run_id

    monkeypatch.setattr(persistence, "_now", lambda: datetime(2026, 10, 1, 0, 1, tzinfo=UTC))
    period = store.reserve_resume_tokens(user_id, run_id, 5)
    store.adjust_tokens(user_id, -2, run_id=run_id, period_override=period)

    rows = store.db.execute(
        "SELECT period,used FROM agent_token_usage WHERE user_id=? ORDER BY period", (user_id,),
    ).fetchall()
    assert rows == [("2026-09", 8), ("2026-10", 3)]
    assert store.usage_for(user_id).tokens.used == 3
