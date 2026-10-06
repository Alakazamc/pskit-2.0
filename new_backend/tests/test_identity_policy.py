import httpx
import pytest

from app.adapters.live.supabase_auth import SupabaseSession
from app.config import Settings
from app.contracts.models import UserIdentity
from app.domain.identity_policy import IdentityPolicyStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app

pytestmark = pytest.mark.usefixtures("live_database")


@pytest.mark.asyncio
async def test_verified_guest_tier_survives_backend_restart(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=object())
    token, guest = first.state.demo_store.issue_anonymous_token()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),
                                 base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json()["is_anonymous"] is True
    assert first.state.identity_policy.tier_for(guest.id) == "guest"

    second = create_app(settings, pi_runner=object())
    assert second.state.identity_policy.tier_for(guest.id) == "guest"
    assert second.state.identity_policy.tier_for("unknown-user") == "guest"


@pytest.mark.asyncio
async def test_verified_member_upgrade_is_visible_across_instances_and_old_guest_token_cannot_downgrade(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=object())
    second = create_app(settings, pi_runner=object())

    class VerifiedUsers:
        async def verify(self, token: str) -> UserIdentity | None:
            if token == "guest-token":
                return UserIdentity(id="same-user", email="", name="Guest", is_anonymous=True)
            if token == "member-token":
                return UserIdentity(id="same-user", email="new@example.org", name="Member",
                                    is_anonymous=False)
            return None

    first.state.identity_provider = VerifiedUsers()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),
                                 base_url="http://test") as client:
        guest = await client.get("/api/v1/me", headers={"Authorization": "Bearer guest-token"})
        upgraded = await client.get("/api/v1/me", headers={"Authorization": "Bearer member-token"})
        stale = await client.get("/api/v1/me", headers={"Authorization": "Bearer guest-token"})

    assert [guest.status_code, upgraded.status_code, stale.status_code] == [200, 200, 200]
    assert second.state.identity_policy.tier_for("same-user") == "member"


def test_existing_project_owner_is_backfilled_as_member_without_changing_project(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    legacy = PersistentConversationStore(path)
    project = legacy.create_project("legacy-user", "Legacy research", "")

    app = create_app(Settings(agent_runtime="pi", agent_db_path=path), pi_runner=object())

    assert app.state.identity_policy.tier_for("legacy-user") == "member"
    assert project.id in [item.id for item in app.state.conversations.projects_for("legacy-user")]


@pytest.mark.asyncio
async def test_email_login_persists_verified_member_tier_without_me_request(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)

    class VerifiedLogin:
        async def sign_in_password(
            self, email: str, password: str, *, client_ip: str | None = None
        ) -> SupabaseSession:
            return SupabaseSession("member-access", "member-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(id="new-member", email="member@example.org", name="Member",
                                is_anonymous=False)

    app.state.identity_provider = VerifiedLogin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/api/v1/auth/login", json={
            "email": "member@example.org", "password": "secret",
        })

    assert response.status_code == 200
    assert app.state.identity_policy.tier_for("new-member") == "member"


@pytest.mark.asyncio
async def test_refresh_observes_anonymous_user_without_me_request(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)

    class VerifiedRefresh:
        async def refresh(self, refresh_token: str) -> SupabaseSession:
            return SupabaseSession("guest-access", "next-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(id="guest-from-refresh", email="", name="Guest",
                                is_anonymous=True)

    app.state.identity_provider = VerifiedRefresh()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        client.cookies.set("research_refresh_token", "old-refresh", path="/api/v1/auth")
        response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert response.json()["user"]["is_anonymous"] is True
    assert app.state.identity_policy.last_seen_for("guest-from-refresh") is not None


@pytest.mark.asyncio
async def test_signed_in_signup_persists_verified_member_tier(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)

    class VerifiedSignup:
        async def sign_up(
            self, email: str, password: str, *, client_ip: str | None = None
        ) -> SupabaseSession:
            return SupabaseSession("signup-access", "signup-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(id="signup-member", email="new@example.org", name="New",
                                is_anonymous=False)

    app.state.identity_provider = VerifiedSignup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/api/v1/auth/signup", json={
            "email": "new@example.org", "password": "12345678",
        })

    assert response.status_code == 200
    assert response.json()["status"] == "signed_in"
    assert app.state.identity_policy.tier_for("signup-member") == "member"


@pytest.mark.asyncio
async def test_email_verification_persists_verified_member_tier(tmp_path):
    settings = Settings(
        mode="live", agent_db_path=str(tmp_path / "agent.sqlite3"),
        supabase_url="https://example.supabase.co", supabase_publishable_key="publishable-test",
    )
    app = create_app(settings)

    class VerifiedOtp:
        async def verify_otp(
            self, email: str, token: str, kind: str, *, client_ip: str | None = None
        ) -> SupabaseSession:
            return SupabaseSession("otp-access", "otp-refresh", 3600)

        async def verify(self, access_token: str) -> UserIdentity:
            return UserIdentity(id="otp-member", email="new@example.org", name="New",
                                is_anonymous=False)

    app.state.identity_provider = VerifiedOtp()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/api/v1/auth/verify", json={
            "email": "new@example.org", "token": "123456", "type": "signup",
        })

    assert response.status_code == 200
    assert app.state.identity_policy.tier_for("otp-member") == "member"


def test_uncertain_identity_flag_does_not_grant_member_capabilities(tmp_path):
    policy = IdentityPolicyStore(str(tmp_path / "identity.sqlite3"))

    policy.observe_verified_user("uncertain-user", None)

    assert policy.tier_for("uncertain-user") == "guest"


@pytest.mark.asyncio
async def test_demo_login_records_member_tier_before_first_protected_request(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})

    assert response.status_code == 200
    assert app.state.identity_policy.tier_for(response.json()["user"]["id"]) == "member"
