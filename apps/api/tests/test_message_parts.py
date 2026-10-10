import sqlite3

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import (
    ArtifactPart,
    ContextRef,
    FilePart,
    MessageRequest,
    ProgressPart,
    TextPart,
    ToolCallPart,
)
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app


def test_persistent_messages_roundtrip_discriminated_parts(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    store = PersistentConversationStore(path)
    accepted = store.accept_message(
        "alice", "session-alice", MessageRequest(content="Review the file", attachments=[
            ContextRef(id="file-1", name="paper.pdf"),
        ]), None, 4, None, "request-hash",
    )
    assert accepted is not None
    run_id = accepted[0].run_id
    parts = [
        TextPart(text="I found a structure."),
        ToolCallPart(tool="search_pdb", status="completed", summary="Found 2 hits"),
        ArtifactPart(id="artifact-1", name="result.cif", kind="structure"),
        ProgressPart(label="Validation", value=100),
    ]
    store.add_assistant_reply("alice", run_id, "I found a structure.", parts=parts)

    reopened = PersistentConversationStore(path)
    messages = reopened.messages_for("alice", "session-alice")
    assert messages is not None
    assert messages[0].parts == [TextPart(text="Review the file"),
                                 FilePart(id="file-1", name="paper.pdf")]
    assert messages[1].parts == parts
    assert reopened.messages_for("bob", "session-alice") is None


def test_existing_text_only_message_table_migrates_without_losing_history(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE agent_messages (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO agent_messages VALUES (?,?,?,?,?,?)",
            ("message-old", "alice", "session-alice", "assistant", "Legacy answer",
             "2026-10-01T00:00:00+00:00"),
        )

    store = PersistentConversationStore(path)
    messages = store.messages_for("alice", "session-alice")
    assert messages is not None and messages[0].parts == [TextPart(text="Legacy answer")]


@pytest.mark.asyncio
async def test_user_file_part_uses_server_name_instead_of_client_display_name(tmp_path):
    app = create_app(Settings(agent_db_path=str(tmp_path / "agent.sqlite3")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={
            "email": "alice@example.org",
        })).json()
        token = identity["access_token"]
        session_id = f"session-{identity['user']['id']}"
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = (await client.post("/api/v1/files", headers=headers, json={
            "name": "actual.txt", "size": 5, "content": "hello",
        })).json()
        await client.post(f"/api/v1/c/{session_id}/messages",
                          headers=headers, json={
                              "content": "Review", "attachments": [{
                                  "id": uploaded["id"], "name": "forged.txt",
                              }],
                          })
        messages = (await client.get(f"/api/v1/c/{session_id}/messages",
                                     headers=headers)).json()

    assert messages[0]["parts"] == [
        {"type": "text", "text": "Review"},
        {"type": "file", "id": uploaded["id"], "name": "actual.txt"},
    ]
