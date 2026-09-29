from __future__ import annotations

import threading
from types import SimpleNamespace

from starlette.requests import Request

from app.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.middleware.auth_abuse import AuthAbuseMiddleware
from app.network import client_ip
from app.rag.qdrant_store import promote_collection
from app.tasks.service import TaskQuotaExceeded, create_queued_task


def test_forwarded_ip_is_only_trusted_from_configured_proxy(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.9.8.1/32")
    trusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-real-ip", b"203.0.113.8")],
            "client": ("10.9.8.1", 1234),
        }
    )
    untrusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-real-ip", b"203.0.113.8")],
            "client": ("198.51.100.7", 1234),
        }
    )
    assert client_ip(trusted) == "203.0.113.8"
    assert client_ip(untrusted) == "198.51.100.7"


def test_auth_rate_limit_is_shared_but_isolated_per_client():
    limiter = AuthAbuseMiddleware(lambda *_args: None, register_limit=2, window_seconds=60)
    assert limiter._limited("register", "203.0.113.1") is None
    assert limiter._limited("register", "203.0.113.1") is None
    assert limiter._limited("register", "203.0.113.1") is not None
    assert limiter._limited("register", "203.0.113.2") is None


def test_task_quota_check_and_insert_are_serialized(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_active_tasks_per_user", 2)
    db = SessionLocal()
    try:
        user = User(username="quota-owner", password_hash="unused", role="user")
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()

    barrier = threading.Barrier(5)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def submit() -> None:
        session = SessionLocal()
        try:
            owner = session.get(User, user_id)
            assert owner is not None
            barrier.wait()
            create_queued_task(
                session,
                owner,
                "predict_interaction",
                {"protein_sequence": "ACDE", "nucleic_sequence": "ACGU"},
            )
            outcome = "created"
        except TaskQuotaExceeded:
            session.rollback()
            outcome = "limited"
        finally:
            session.close()
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=submit) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert outcomes.count("created") == 2
    assert outcomes.count("limited") == 3


def test_qdrant_alias_swap_does_not_delete_active_collection():
    class FakeClient:
        def __init__(self):
            self.operations = []
            self.deleted = []

        def get_aliases(self):
            return SimpleNamespace(
                aliases=[SimpleNamespace(alias_name="knowledge", collection_name="old-build")]
            )

        def update_collection_aliases(self, *, change_aliases_operations):
            self.operations.append(change_aliases_operations)
            return True

        def delete_collection(self, *, collection_name):
            self.deleted.append(collection_name)

    client = FakeClient()
    previous = promote_collection(
        client,
        "knowledge",
        "new-build",
        vector_size=3,
        chunks=[],
        vectors=[],
    )
    assert previous == "old-build"
    assert len(client.operations) == 1
    assert client.deleted == []
