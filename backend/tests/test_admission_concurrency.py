import multiprocessing
import os
import time

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import AuthSession, Task, User
from app.db.session import SessionLocal


def _submit_in_process(user_id, kind, barrier, outcomes):
    """Separate interpreters model API/worker containers sharing one SQLite file."""
    from app.auth import sessions
    from app.tasks import service

    get_settings().max_active_tasks_per_user = 2
    os.environ["PSKIT_MAX_ACTIVE_SESSIONS_PER_USER"] = "2"
    original_check = service._enforce_task_quotas
    original_token = sessions.make_session_token

    def slow_check(*args):
        original_check(*args)
        time.sleep(0.2)  # Expose the check/insert race without a database lock.

    def slow_token():
        time.sleep(0.2)
        return original_token()

    service._enforce_task_quotas = slow_check
    sessions.make_session_token = slow_token
    with SessionLocal() as db:
        try:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            if kind == "task":
                service.create_queued_task(
                    db, user, "predict_interaction",
                    {"protein_sequence": "ACDE", "nucleic_sequence": "ACGU"},
                )
            else:
                sessions.create_session(db, user)
            outcomes.put("created")
        except service.TaskQuotaExceeded:
            db.rollback()
            outcomes.put("limited")
        except Exception as exc:
            outcomes.put(f"{type(exc).__name__}: {exc}")


def _parallel_submissions(kind):
    with SessionLocal() as db:
        user = User(username=f"parallel-{kind}", password_hash="unused", role="user")
        db.add(user)
        db.commit()
        user_id = user.id
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    outcomes = context.Queue()
    processes = [
        context.Process(target=_submit_in_process, args=(user_id, kind, barrier, outcomes))
        for _ in range(4)
    ]
    try:
        for process in processes:
            process.start()
        results = [outcomes.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        outcomes.close()


def test_task_quota_is_enforced_across_processes():
    results = _parallel_submissions("task")
    assert sorted(results) == ["created", "created", "limited", "limited"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 2


def test_auth_session_limit_is_enforced_across_processes():
    assert _parallel_submissions("auth") == ["created"] * 4
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 2
