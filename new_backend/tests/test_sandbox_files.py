"""Public artifact IDs retain ownership and work with existing catalog consumers."""

import sqlite3

import httpx
import pytest
from fastapi import FastAPI

from app.api import catalog, sandbox_files
from app.api.auth import get_current_user
from app.contracts.models import UserIdentity
from app.domain.catalog import ContextNotFound
from app.domain.sandboxes import SandboxArtifactStore


@pytest.fixture
def artifacts():
    database = sqlite3.connect(":memory:")
    database.executescript(
        "CREATE TABLE agent_artifact_blobs (id TEXT PRIMARY KEY,user_id TEXT,"
        "job_id TEXT,name TEXT,kind TEXT,size INTEGER,sha256 TEXT,content BLOB,created_at TEXT);"
        "CREATE TABLE sandbox_artifacts(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,"
        "attempt_id TEXT,name TEXT,sha256 TEXT);"
        "CREATE TABLE catalog_files(user_id TEXT,size INTEGER)"
    )
    return SandboxArtifactStore(database)


@pytest.mark.asyncio
async def test_output_roundtrip_preserves_artifact_owner_at_public_download(artifacts):
    ref = artifacts.put("alice", "one", "attempt", "result.txt", b"result")
    assert artifacts.put("alice", "one", "attempt", "result.txt", b"result") == ref

    class Transfer:
        def __init__(self):
            self.artifacts = artifacts

        async def prepare(self, user, session, files):
            raise ContextNotFound()

    class AF3:
        def artifacts_for(self, user):
            return []

        def artifact_bytes_for(self, user, artifact):
            return None

    app = FastAPI()
    app.state.workspace_transfer = Transfer()
    app.state.af3 = AF3()
    app.include_router(sandbox_files.router)
    app.include_router(catalog.router)

    async def alice():
        return UserIdentity(id="alice", email="alice@test", name="Alice")

    async def bob():
        return UserIdentity(id="bob", email="bob@test", name="Bob")

    app.dependency_overrides[get_current_user] = alice
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as client:
        assert (await client.get(f"/api/v1/artifacts/{ref.id}/download")).content == b"result"
        assert (await client.get("/api/v1/artifacts")).json()[0]["id"] == ref.id
        assert (await client.get(f"/api/v1/artifacts/{ref.id}/preview")).json()["text"] == "result"
        app.dependency_overrides[get_current_user] = bob
        assert (await client.get(f"/api/v1/artifacts/{ref.id}/download")).status_code == 404
        assert (await client.get("/api/v1/artifacts")).json() == []
        assert (
            await client.post("/api/v1/sandbox/sessions/one/files", json={"file_ids": ["id"]})
        ).status_code == 404
    assert len(artifacts.list("alice")) == 1


def test_artifact_admission_enforces_storage_cap_without_charging_replay(artifacts):
    from app.domain.catalog import InvalidFileUpload

    artifacts.storage_limit_for = lambda _: 4
    existing = artifacts.put("alice", "one", "attempt", "first.txt", b"four")
    with pytest.raises(InvalidFileUpload):
        artifacts.put("alice", "one", "attempt", "second.txt", b"x")
    artifacts.storage_limit_for = lambda _: 0
    assert artifacts.put("alice", "one", "attempt", "first.txt", b"four") == existing
    assert len(artifacts.list("alice")) == 1
