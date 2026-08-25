from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.db.models import Task, TaskExecutionLease, TaskRetry, User, now_utc
from app.db.session import SessionLocal
from app.tasks.worker import claim_next_task, recover_stale_tasks
from app.tools.mcp_adapters import MCPAdapterError, extract_mcp_result


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
        assert claimed_first.output_json["_worker_attempt"] == 1
        assert claimed_second.output_json["_worker_attempt"] == 1
        assert db.get(TaskExecutionLease, claimed_first.id) is not None
        assert db.get(TaskExecutionLease, claimed_second.id) is not None
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
            updated_at=now_utc() - timedelta(hours=2),
        )
        db.add(task)
        db.commit()
        monkeypatch.setattr(worker.get_settings(), "task_stale_after_seconds", 1)

        assert recover_stale_tasks(db) == 1
        db.refresh(task)
        assert task.status == "failed"
        assert task.error_type == "TaskLeaseExpired"
        retry = db.get(TaskRetry, task.id)
        assert retry is not None
        child = db.get(Task, retry.child_task_id)
        assert child is not None and child.status == "queued"
    finally:
        db.close()


def test_mcp_result_to_dict_prefers_structured_content():
    result = SimpleNamespace(isError=False, structuredContent={"score": 0.9}, content=[])
    assert extract_mcp_result(result, source_label="test") == {"score": 0.9}


def test_mcp_result_to_dict_rejects_remote_errors():
    result = SimpleNamespace(isError=True, structuredContent=None, content=[])
    with pytest.raises(MCPAdapterError):
        extract_mcp_result(result, source_label="test")
