"""Behavior tests for conservative anonymous-account cleanup."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.adapters.live.supabase_admin import SupabaseGuestAdmin
from app.config import Settings
from app.contracts.models import UserIdentity
from app.contracts.conversation import MessageRequest
from app.domain.identity_policy import GuestAccountDeleting, IdentityPolicyStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app
from app.services.guest_cleanup import GuestCleanupService
from scripts.cleanup_guests import main as cleanup_main


def _age_user(policy: IdentityPolicyStore, user_id: str, days: int = 31) -> None:
    policy.db.execute(
        "UPDATE account_tiers SET last_seen_at=? WHERE user_id=?",
        ((datetime.now(UTC) - timedelta(days=days)).isoformat(), user_id),
    )
    policy.db.commit()


def test_candidates_exclude_active_guests_members_and_guests_with_pending_work(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    conversations = PersistentConversationStore(path)
    policy = IdentityPolicyStore(path)
    for user_id in ("stale", "recent", "member", "running", "queued-job"):
        policy.observe_verified_user(user_id, user_id != "member")
    for user_id in ("stale", "member", "running", "queued-job"):
        _age_user(policy, user_id)
    conversations.db.execute(
        "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) "
        "VALUES ('run-1','running','session-1','running',?)",
        (datetime.now(UTC).isoformat(),),
    )
    conversations.db.execute(
        "INSERT INTO agent_jobs (id,user_id,status,progress,estimated_minutes,created_at) "
        "VALUES ('job-1','queued-job','queued',0,5,?)",
        (datetime.now(UTC).isoformat(),),
    )
    conversations.db.execute(
        "INSERT INTO agent_approvals "
        "(id,user_id,run_id,tool_call_id,estimated_minutes,status,created_at) "
        "VALUES ('approval-1','stale','old-run','old-tool',5,'approved',?)",
        (datetime.now(UTC).isoformat(),),
    )
    conversations.db.commit()

    service = GuestCleanupService(path, admin=None)
    assert service.collect_candidates(datetime.now(UTC)) == ["stale"]


@pytest.mark.asyncio
async def test_cleanup_requires_admin_and_deletes_only_verified_anonymous_account(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    conversations = PersistentConversationStore(path)
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    project = conversations.create_project("guest-1", "Private project", "")
    conversations.db.execute(
        "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) "
        "VALUES ('finished-run','guest-1','session-1','completed',?)",
        (datetime.now(UTC).isoformat(),),
    )
    conversations.db.commit()
    mcp_calls = McpToolCallStore(path)
    mcp_calls.claim("finished-run", "tool-1", "search", {})

    without_admin = GuestCleanupService(path, admin=None)
    assert await without_admin.purge_one("guest-1") is False
    assert policy.tier_for("guest-1") == "guest"

    class Admin:
        deleted = []

        async def get_user(self, user_id):
            return {"id": user_id, "is_anonymous": True}

        async def delete_user(self, user_id):
            self.deleted.append(user_id)

    admin = Admin()
    service = GuestCleanupService(path, admin=admin)
    assert await service.purge_one("guest-1") is True
    assert await service.purge_one("guest-1") is False
    assert admin.deleted == ["guest-1"]
    assert project.id not in [item.id for item in conversations.projects_for("guest-1")]
    assert policy.db.execute("SELECT 1 FROM account_tiers WHERE user_id='guest-1'").fetchone() is None
    assert mcp_calls.db.execute(
        "SELECT 1 FROM mcp_tool_calls WHERE run_id='finished-run'",
    ).fetchone() is None
    with pytest.raises(GuestAccountDeleting):
        policy.observe_verified_user("guest-1", True)


@pytest.mark.asyncio
async def test_two_instances_cannot_claim_the_same_guest(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    entered = asyncio.Event()
    resume = asyncio.Event()

    class SlowAdmin:
        calls = 0

        async def get_user(self, user_id):
            self.calls += 1
            entered.set()
            await resume.wait()
            return {"id": user_id, "is_anonymous": True}

        async def delete_user(self, user_id):
            pass

    admin = SlowAdmin()
    first = GuestCleanupService(path, admin)
    second = GuestCleanupService(path, admin)
    running = asyncio.create_task(first.purge_one("guest-1"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert await second.purge_one("guest-1") is False
    resume.set()
    assert await running is True
    assert admin.calls == 1


@pytest.mark.asyncio
async def test_remote_delete_can_be_retried_after_local_failure(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    conversations = PersistentConversationStore(path)
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    conversations.create_project("guest-1", "Research", "")
    conversations.db.execute(
        "CREATE TRIGGER block_guest_cleanup BEFORE DELETE ON workspace_projects "
        "BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END",
    )
    conversations.db.commit()

    class Admin:
        checks = 0
        deletes = 0

        async def get_user(self, user_id):
            self.checks += 1
            return {"id": user_id, "is_anonymous": True}

        async def delete_user(self, user_id):
            self.deletes += 1

    admin = Admin()
    service = GuestCleanupService(path, admin)
    assert await service.purge_one("guest-1") is False
    assert policy.db.execute(
        "SELECT cleanup_state FROM account_tiers WHERE user_id='guest-1'",
    ).fetchone()[0] == "deleting"
    assert service.collect_candidates(datetime.now(UTC)) == ["guest-1"]
    assert policy.db.execute(
        "SELECT outcome FROM guest_cleanup_audit ORDER BY id DESC LIMIT 1",
    ).fetchone()[0] == "retry"
    conversations.db.execute("DROP TRIGGER block_guest_cleanup")
    conversations.db.commit()
    assert await service.purge_one("guest-1") is True
    assert (admin.checks, admin.deletes) == (1, 1)


@pytest.mark.asyncio
async def test_member_on_remote_recheck_is_never_deleted(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")

    class Admin:
        async def get_user(self, user_id):
            return {"id": user_id, "is_anonymous": False}

        async def delete_user(self, user_id):
            pytest.fail("Member must not be deleted")

    assert await GuestCleanupService(path, Admin()).purge_one("guest-1") is False
    assert policy.tier_for("guest-1") == "member"


@pytest.mark.asyncio
async def test_mismatched_remote_identity_cannot_promote_or_delete_guest(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")

    class Admin:
        async def get_user(self, user_id):
            return {"id": "someone-else", "is_anonymous": False}

        async def delete_user(self, user_id):
            pytest.fail("Mismatched identity must not be deleted")

    assert await GuestCleanupService(path, Admin()).purge_one("guest-1") is False
    assert policy.tier_for("guest-1") == "guest"


@pytest.mark.asyncio
async def test_claimed_guest_cannot_use_api_or_start_upgrade(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    app = create_app(Settings(
        mode="live", agent_db_path=path,
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable-test",
    ))
    app.state.identity_policy.observe_verified_user("guest-1", True)
    _age_user(app.state.identity_policy, "guest-1")

    class Provider:
        async def verify(self, token):
            return UserIdentity(id="guest-1", email="", name="Guest", is_anonymous=True)

    entered = asyncio.Event()
    resume = asyncio.Event()

    class Admin:
        async def get_user(self, user_id):
            entered.set()
            await resume.wait()
            return {"id": user_id, "is_anonymous": True}

        async def delete_user(self, user_id):
            pass

    app.state.identity_provider = Provider()
    running = asyncio.create_task(GuestCleanupService(path, Admin()).purge_one("guest-1"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers={"Authorization": "Bearer guest"})
        upgrade = await client.post("/api/v1/auth/upgrade/email", json={
            "email": "new@example.org",
        }, headers={"Authorization": "Bearer guest"})
    resume.set()
    await running
    assert me.status_code == 410
    assert me.json()["detail"]["code"] == "GUEST_ACCOUNT_DELETING"
    assert upgrade.status_code == 410


@pytest.mark.asyncio
async def test_run_admission_rechecks_cleanup_lock_in_the_same_database_transaction(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    conversations = PersistentConversationStore(path)
    policy = IdentityPolicyStore(path)
    conversations.identity_policy = policy
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    session = conversations.create_session("guest-1", conversations.project_for("guest-1").id, "Study")
    assert session is not None

    class Admin:
        async def get_user(self, user_id):
            return None

        async def delete_user(self, user_id):
            pass

    service = GuestCleanupService(path, Admin())
    claim = service._claim("guest-1", datetime.now(UTC))
    assert claim is not None
    with pytest.raises(GuestAccountDeleting):
        conversations.send_message("guest-1", session.id, MessageRequest(content="hello"))
    assert conversations.db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE user_id='guest-1'",
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_supabase_guest_admin_uses_server_secret_and_accepts_missing_user():
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "guest-1", "is_anonymous": True})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        admin = SupabaseGuestAdmin("https://example.supabase.co", "sb_secret_test", client)
        assert await admin.get_user("guest-1") == {
            "id": "guest-1", "is_anonymous": True,
        }
        await admin.delete_user("guest-1")

    assert [request.method for request in requests] == ["GET", "DELETE"]
    assert all(request.url.path == "/auth/v1/admin/users/guest-1" for request in requests)
    assert all(request.headers["apikey"] == "sb_secret_test" for request in requests)
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_cleanup_removes_owned_pi_session_files(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    sessions_root = tmp_path / "pi-sessions"
    owned = sessions_root / "session-1"
    owned.mkdir(parents=True)
    (owned / "turn.jsonl").write_text("private transcript")
    other = sessions_root / "session-2"
    other.mkdir()
    (other / "turn.jsonl").write_text("other transcript")
    conversations = PersistentConversationStore(path)
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    conversations.db.execute(
        "INSERT INTO pi_sessions (session_id,user_id,session_file) VALUES (?,?,?)",
        ("session-1", "guest-1", str(owned / "turn.jsonl")),
    )
    conversations.db.commit()

    class Admin:
        async def get_user(self, user_id):
            return {"id": user_id, "is_anonymous": True}

        async def delete_user(self, user_id):
            pass

    service = GuestCleanupService(path, Admin(), pi_session_dir=str(sessions_root))
    assert await service.purge_one("guest-1") is True
    assert not owned.exists()
    assert (other / "turn.jsonl").read_text() == "other transcript"


def test_cleanup_script_defaults_to_dry_run_and_requires_live_admin_key(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "agent.sqlite3")
    policy = IdentityPolicyStore(path)
    policy.observe_verified_user("guest-1", True)
    _age_user(policy, "guest-1")
    monkeypatch.setenv("RESEARCH_AGENT_DB_PATH", path)
    monkeypatch.setenv("RESEARCH_AGENT_MODE", "live")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "publishable-test")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)

    assert cleanup_main([]) == 0
    assert "dry run" in capsys.readouterr().out.lower()
    assert policy.db.execute(
        "SELECT 1 FROM account_tiers WHERE user_id='guest-1'",
    ).fetchone() is not None
    assert cleanup_main(["--execute"]) == 2
    assert policy.db.execute(
        "SELECT 1 FROM account_tiers WHERE user_id='guest-1'",
    ).fetchone() is not None
