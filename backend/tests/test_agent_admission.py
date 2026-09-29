from __future__ import annotations

import threading
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select

from app.api.agent import send_message
from app.agent.execution import AgentDispatchHeartbeat, AgentExecutionError
from app.config import get_settings
from app.db.models import AgentSession, AgentTurn, User, now_utc
from app.db.session import SessionLocal
from app.schemas.agent import AgentMessageRequest


def test_late_original_dispatch_cannot_replace_healthy_recovery_owner():
    user_id, session_ids = _create_user_with_sessions("dispatch-race", 1)
    _submit(user_id, session_ids[0], uuid4())
    with SessionLocal() as db:
        turn = db.scalar(select(AgentTurn).where(AgentTurn.user_id == user_id))
        turn.lease_owner = "dispatcher:recovery"
        db.commit()
        turn_id = turn.id
    recovery = AgentDispatchHeartbeat(turn_id)
    recovery.start()
    try:
        late_original = AgentDispatchHeartbeat(turn_id)
        with pytest.raises(AgentExecutionError):
            late_original.start()
        with SessionLocal() as db:
            assert db.get(AgentTurn, turn_id).lease_owner == recovery.owner
    finally:
        recovery.stop()


def _create_user_with_sessions(username: str, count: int) -> tuple[UUID, list[UUID]]:
    db = SessionLocal()
    try:
        user = User(username=username, password_hash="unused", role="user")
        db.add(user)
        db.flush()
        sessions = [
            AgentSession(user_id=user.id, title=f"session-{index}")
            for index in range(count)
        ]
        db.add_all(sessions)
        db.commit()
        return user.id, [session.id for session in sessions]
    finally:
        db.close()


def _submit(user_id: UUID, session_id: UUID, turn_id: UUID, content: str = "hello"):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        return send_message(
            session_id,
            AgentMessageRequest(content=content, turn_id=turn_id),
            db,
            user,
        )
    finally:
        db.close()


def test_agent_turn_user_limit_is_serialized_across_connections(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_active_agent_turns_per_user", 2)
    monkeypatch.setattr(settings, "max_global_active_agent_turns", 10)
    user_id, session_ids = _create_user_with_sessions("agent-quota-owner", 5)
    barrier = threading.Barrier(len(session_ids))
    outcomes: list[int] = []
    outcomes_lock = threading.Lock()

    def submit(session_id: UUID) -> None:
        try:
            barrier.wait()
            response = _submit(user_id, session_id, uuid4())
            outcome = response.status_code
        except HTTPException as exc:
            outcome = exc.status_code
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=submit, args=(session_id,)) for session_id in session_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert outcomes.count(status.HTTP_200_OK) == 2
    assert outcomes.count(status.HTTP_429_TOO_MANY_REQUESTS) == 3

    db = SessionLocal()
    try:
        active = db.query(AgentTurn).filter(AgentTurn.status.in_(("queued", "running"))).count()
        assert active == 2
    finally:
        db.close()


def test_global_limit_returns_clear_429_but_allows_idempotent_replay(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_active_agent_turns_per_user", 10)
    monkeypatch.setattr(settings, "max_global_active_agent_turns", 1)
    first_user_id, first_sessions = _create_user_with_sessions("agent-global-first", 1)
    second_user_id, second_sessions = _create_user_with_sessions("agent-global-second", 1)
    original_turn_id = uuid4()

    created = _submit(first_user_id, first_sessions[0], original_turn_id)
    assert created.status_code == status.HTTP_200_OK

    with pytest.raises(HTTPException) as limited:
        _submit(second_user_id, second_sessions[0], uuid4())
    assert limited.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert limited.value.headers == {"Retry-After": "5"}
    assert limited.value.detail == {
        "error_type": "agent_turn_global_limit",
        "message": "Agent 任务队列已满，请稍后重试。",
        "limit": 1,
    }

    replay = _submit(first_user_id, first_sessions[0], original_turn_id)
    assert replay.status_code == status.HTTP_200_OK


def test_admission_reclaims_expired_running_turn_from_unvisited_session(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_active_agent_turns_per_user", 10)
    monkeypatch.setattr(settings, "max_global_active_agent_turns", 1)
    first_user_id, first_sessions = _create_user_with_sessions("agent-stale-first", 1)
    second_user_id, second_sessions = _create_user_with_sessions("agent-stale-second", 1)
    stale_turn_id = uuid4()
    _submit(first_user_id, first_sessions[0], stale_turn_id)

    db = SessionLocal()
    try:
        stale_turn = db.query(AgentTurn).filter_by(client_turn_id=stale_turn_id).one()
        stale_turn.status = "running"
        stale_turn.lease_token = "expired-worker"
        stale_turn.lease_owner = "stopped-server"
        stale_turn.lease_expires_at = now_utc() - timedelta(seconds=1)
        stale_turn.updated_at = now_utc() - timedelta(
            seconds=settings.agent_turn_stale_seconds + 1
        )
        db.commit()
        stale_server_turn_id = stale_turn.id
    finally:
        db.close()

    admitted = _submit(second_user_id, second_sessions[0], uuid4())
    assert admitted.status_code == status.HTTP_200_OK

    db = SessionLocal()
    try:
        expired = db.get(AgentTurn, stale_server_turn_id)
        assert expired is not None
        assert expired.status == "failed"
        assert expired.error_code == "agent_turn_stale"
    finally:
        db.close()


def test_admission_reclaims_orphaned_queued_dispatch_after_restart(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_active_agent_turns_per_user", 10)
    monkeypatch.setattr(settings, "max_global_active_agent_turns", 1)
    first_user_id, first_sessions = _create_user_with_sessions("agent-orphan-first", 1)
    second_user_id, second_sessions = _create_user_with_sessions("agent-orphan-second", 1)
    orphan_turn_id = uuid4()
    _submit(first_user_id, first_sessions[0], orphan_turn_id)

    db = SessionLocal()
    try:
        orphan = db.query(AgentTurn).filter_by(client_turn_id=orphan_turn_id).one()
        orphan.lease_owner = "dispatcher:stopped-server"
        orphan.lease_expires_at = now_utc() - timedelta(seconds=1)
        # The dispatch lease may expire soon after a crash, while the turn's
        # updated_at is still recent. It must not occupy quota for 15 minutes.
        orphan.updated_at = now_utc()
        db.commit()
        orphan_server_turn_id = orphan.id
    finally:
        db.close()

    admitted = _submit(second_user_id, second_sessions[0], uuid4())
    assert admitted.status_code == status.HTTP_200_OK

    db = SessionLocal()
    try:
        expired = db.get(AgentTurn, orphan_server_turn_id)
        assert expired is not None
        assert expired.status == "failed"
        assert expired.error_code == "agent_turn_dispatch_stale"
    finally:
        db.close()


def test_admission_preserves_queued_turn_with_local_future(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_active_agent_turns_per_user", 10)
    monkeypatch.setattr(settings, "max_global_active_agent_turns", 1)
    first_user_id, first_sessions = _create_user_with_sessions("agent-local-future", 1)
    second_user_id, second_sessions = _create_user_with_sessions("agent-local-waiter", 1)
    _submit(first_user_id, first_sessions[0], uuid4())

    with SessionLocal() as db:
        queued = db.scalar(select(AgentTurn).where(AgentTurn.user_id == first_user_id))
        assert queued is not None
        queued_id = queued.id
        queued.lease_expires_at = now_utc() - timedelta(seconds=1)
        db.commit()

    monkeypatch.setattr("app.api.agent.is_agent_turn_scheduled", lambda turn_id: turn_id == queued_id)
    with pytest.raises(HTTPException) as limited:
        _submit(second_user_id, second_sessions[0], uuid4())
    assert limited.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    with SessionLocal() as db:
        preserved = db.get(AgentTurn, queued_id)
        assert preserved is not None
        assert preserved.status == "queued"
        assert preserved.active_session_key is not None
