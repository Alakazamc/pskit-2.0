"""Old server-key maintenance remains compatible and leaves safe operator audit."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_legacy_skill_mutations_have_safe_operator_audit(tmp_path):
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=str(tmp_path / "legacy.db"),
            admin_api_key="old-server-key",
        ),
        pi_runner=object(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "auditor@example.org"})
        ).json()
        uid = login["user"]["id"]
        jwt = {"Authorization": f"Bearer {login['access_token']}"}
        key = {"X-Admin-Key": "old-server-key"}
        app.state.admin_store.grant(
            uid, "auditor", actor="server:test", reason="Audit fixture grant"
        )
        saved = await client.put(
            "/api/v1/admin/skills/lab-review/versions/1",
            headers=key,
            json={
                "name": "Lab review",
                "description": "Review science outputs",
                "tools": ["search_pdb"],
                "instructions": "Sensitive internal instruction text",
            },
        )
        assert saved.status_code == 200
        grants = await client.put(
            f"/api/v1/admin/users/{uid}/skills", headers=key, json={"skill_ids": ["lab-review"]}
        )
        assert grants.status_code == 200 and grants.json() == {"skill_ids": ["lab-review"]}
        events = await client.get("/api/v1/admin/audit-events", headers=jwt)
        assert {"skills:register", "skills:grants"} <= {e["action"] for e in events.json()["items"]}
        assert "Sensitive internal instruction text" not in events.text
        assert "old-server-key" not in events.text
        assert {
            e["actor_user_id"] for e in events.json()["items"] if e["action"].startswith("skills:")
        } == {"operator:legacy-admin-key"}


@pytest.mark.asyncio
async def test_legacy_quota_write_preserves_unconfigured_storage(tmp_path):
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=str(tmp_path / "legacy.db"),
            admin_api_key="old-server-key",
        ),
        pi_runner=object(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = (
            await client.post("/api/v1/auth/demo", json={"email": "member@example.org"})
        ).json()
        uid = login["user"]["id"]
        jwt = {"Authorization": f"Bearer {login['access_token']}"}
        app.state.admin_store.grant(
            uid, "platform_admin", actor="server:test", reason="Admin fixture grant"
        )
        result = await client.put(
            f"/api/v1/admin/users/{uid}/limits",
            headers={"X-Admin-Key": "old-server-key"},
            json={"token_monthly_limit": 100, "gpu_daily_minutes": 10},
        )
        assert result.status_code == 200 and result.json()["tokens"]["limit"] == 100
        current = (await client.get(f"/api/v1/admin/users/{uid}/limits", headers=jwt)).json()
        assert current["storage_limit_bytes"] is None
        assert (
            await client.post(
                "/api/v1/files",
                headers=jwt,
                json={"name": "note.md", "size": 5, "content": "hello"},
            )
        ).status_code == 200
