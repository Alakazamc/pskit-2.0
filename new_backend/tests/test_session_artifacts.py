"""Conversation artifact lists include only outputs from an owned session."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.domain.sandboxes import SandboxArtifactStore
from app.main import create_app


@pytest.mark.asyncio
async def test_session_artifacts_are_scoped_to_the_owned_conversation(tmp_path):
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object(),
    )
    app.state.conversations.db.execute(
        "CREATE TABLE sandbox_artifacts(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,"
        "attempt_id TEXT,name TEXT,sha256 TEXT)"
    )
    app.state.workspace_transfer = SimpleNamespace(
        artifacts=SandboxArtifactStore(app.state.conversations.db)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        first = (await client.post("/api/v1/c", headers=alice_headers, json={"title": "First"})).json()
        second = (await client.post("/api/v1/c", headers=alice_headers, json={"title": "Second"})).json()
        first_artifact = app.state.workspace_transfer.artifacts.put(
            alice["user"]["id"], first["id"], "attempt-1", "first.txt", b"first"
        )
        second_artifact = app.state.workspace_transfer.artifacts.put(
            alice["user"]["id"], second["id"], "attempt-2", "second.txt", b"second"
        )
        for session, suffix in ((first, "one"), (second, "two")):
            content = suffix.encode()
            app.state.conversations.db.execute(
                "INSERT INTO agent_runs(id,user_id,session_id,status,created_at,context_json) "
                "VALUES (?,?,?,?,?,?)",
                (f"run-{suffix}", alice["user"]["id"], session["id"], "completed",
                 datetime.now(UTC).isoformat(), "{}"),
            )
            app.state.conversations.db.execute(
                "INSERT INTO agent_jobs(id,user_id,run_id,status,progress,estimated_minutes,"
                "created_at,artifacts) VALUES (?,?,?,?,?,?,?,?)",
                (f"job-{suffix}", alice["user"]["id"], f"run-{suffix}", "completed", 100,
                 1, datetime.now(UTC).isoformat(), json.dumps([{
                     "id": f"af3-{suffix}", "name": f"model-{suffix}.cif", "kind": "structure"
                 }])),
            )
            app.state.conversations.db.execute(
                "INSERT INTO agent_artifact_blobs "
                "(id,user_id,job_id,name,kind,size,sha256,content,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"af3-{suffix}", alice["user"]["id"], f"job-{suffix}",
                 f"model-{suffix}.cif", "structure", len(content),
                 hashlib.sha256(content).hexdigest(), content, datetime.now(UTC).isoformat()),
            )
        app.state.conversations.db.commit()

        own = await client.get(f"/api/v1/sessions/{first['id']}/artifacts", headers=alice_headers)
        other = await client.get(f"/api/v1/sessions/{second['id']}/artifacts", headers=alice_headers)
        foreign = await client.get(f"/api/v1/sessions/{first['id']}/artifacts", headers=bob_headers)
        anonymous = await client.get(f"/api/v1/sessions/{first['id']}/artifacts")

    assert own.status_code == other.status_code == 200
    assert [item["id"] for item in own.json()] == ["af3-one", first_artifact.id]
    assert [item["id"] for item in other.json()] == ["af3-two", second_artifact.id]
    assert foreign.status_code == 404
    assert anonymous.status_code == 401
