from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.db.models import Task, User, now_utc
from app.db.session import SessionLocal
from app.tasks.worker import (
    TaskWorkerError,
    claim_next_task,
    mcp_result_to_dict,
    recover_stale_tasks,
)


def test_claims_each_task_once():
    db = SessionLocal()
    try:
        user = User(username="queue_owner", password_hash="unused", role="user")
        db.add(user)
        db.commit()
        db.refresh(user)
        first = Task(user_id=user.id, task_type="predict_interaction", status="queued")
        second = Task(user_id=user.id, task_type="predict_interaction", status="queued")
        db.add_all([first, second])
        db.commit()

        claimed_first = claim_next_task(db)
        claimed_second = claim_next_task(db)
        assert claimed_first is not None
        assert claimed_second is not None
        assert claimed_first.id != claimed_second.id
        assert claimed_first.attempt_count == 1
        assert claimed_second.attempt_count == 1
        assert claim_next_task(db) is None
    finally:
        db.close()


def test_recovers_stale_running_task(monkeypatch):
    from app.tasks import worker

    db = SessionLocal()
    try:
        user = User(username="recovery_owner", password_hash="unused", role="user")
        db.add(user)
        db.commit()
        db.refresh(user)
        task = Task(
            user_id=user.id,
            task_type="predict_interaction",
            status="running",
            attempt_count=1,
            started_at=now_utc() - timedelta(hours=2),
        )
        db.add(task)
        db.commit()
        monkeypatch.setattr(worker.get_settings(), "task_subprocess_timeout_seconds", 1)
        monkeypatch.setattr(worker.get_settings(), "task_stale_grace_seconds", 1)

        assert recover_stale_tasks(db) == 1
        db.refresh(task)
        assert task.status == "queued"
        assert task.error_type == "WorkerInterrupted"
    finally:
        db.close()


def test_mcp_result_to_dict_prefers_structured_content():
    result = SimpleNamespace(isError=False, structuredContent={"score": 0.9}, content=[])
    assert mcp_result_to_dict(result) == {"score": 0.9}


def test_mcp_result_to_dict_rejects_remote_errors():
    result = SimpleNamespace(isError=True, structuredContent=None, content=[])
    with pytest.raises(TaskWorkerError):
        mcp_result_to_dict(result)
