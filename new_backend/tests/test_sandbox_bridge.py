"""The private sandbox HTTP boundary preserves Pi RPC events and ownership."""

import base64
import json

import httpx
import pytest

from app.sandbox_bridge import create_bridge_app


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path / "sessions"))


class RecordingRunner:
    def __init__(self):
        self.calls = []

    async def prompt(self, session_id, message, on_event, **kwargs):
        self.calls.append((session_id, message, kwargs))
        await on_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "完成",
                },
            }
        )
        return {"session_file": f"/workspace/sessions/{session_id}/turn.jsonl", "text": "完成"}


@pytest.mark.asyncio
async def test_sandbox_bridge_only_runs_owners_pi_and_streams_result():
    runner = RecordingRunner()
    app = create_bridge_app("user-123", "bridge-secret", lambda _: runner)
    transport = httpx.ASGITransport(app=app)
    payload = {
        "user_id": "user-123",
        "session_id": "session-abc",
        "message": "分析文件",
        "environment": {"PSKIT_RUN_ID": "run-1"},
        "model": "claude-opus-4-8",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as client:
        rejected = await client.post("/v1/pi/prompt", json=payload)
        assert rejected.status_code == 401
        wrong_owner = await client.post(
            "/v1/pi/prompt",
            json={**payload, "user_id": "other-user"},
            headers={"Authorization": "Bearer bridge-secret"},
        )
        assert wrong_owner.status_code == 403
        response = await client.post(
            "/v1/pi/prompt",
            json=payload,
            headers={"Authorization": "Bearer bridge-secret"},
        )

    assert response.status_code == 200
    records = [json.loads(line) for line in response.text.splitlines()]
    assert [record["kind"] for record in records] == ["event", "result"]
    assert records[0]["data"]["assistantMessageEvent"]["delta"] == "完成"
    assert records[1]["data"] == {
        "session_file": "/workspace/sessions/session-abc/turn.jsonl",
        "text": "完成",
    }
    assert runner.calls[0][0:2] == ("session-abc", "分析文件")


@pytest.mark.asyncio
async def test_sandbox_bridge_rejects_path_traversal_before_starting_pi():
    runner = RecordingRunner()
    app = create_bridge_app("user-123", "bridge-secret", lambda _: runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    ) as client:
        response = await client.post(
            "/v1/pi/prompt",
            json={
                "user_id": "user-123",
                "session_id": "../other",
                "message": "hi",
                "model": "m",
            },
            headers={"Authorization": "Bearer bridge-secret"},
        )
    assert response.status_code == 422
    assert runner.calls == []


@pytest.mark.asyncio
async def test_sandbox_bridge_forwards_valid_image_and_rejects_invalid_image():
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )

    runner = RecordingRunner()
    app = create_bridge_app("user-123", "bridge-secret", lambda _: runner)
    payload = {
        "user_id": "user-123",
        "session_id": "session-abc",
        "message": "Describe",
        "model": "vision-model",
    }
    headers = {"Authorization": "Bearer bridge-secret"}
    image = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    ) as client:
        invalid = await client.post(
            "/v1/pi/prompt",
            json={
                **payload,
                "images": [
                    {**image, "data": base64.b64encode(b"fake").decode()},
                ],
            },
            headers=headers,
        )
        accepted = await client.post(
            "/v1/pi/prompt", json={**payload, "images": [image]}, headers=headers
        )
    assert invalid.status_code == 422
    assert accepted.status_code == 200
    assert runner.calls[0][2]["images"] == [image]


@pytest.mark.asyncio
async def test_bridge_preserves_ten_image_attachments():
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    image = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}
    runner = RecordingRunner()
    app = create_bridge_app("alice", "secret", lambda _: runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.post(
            "/v1/pi/prompt",
            headers={"Authorization": "Bearer secret"},
            json={
                "user_id": "alice",
                "session_id": "one",
                "attempt_id": "ten-images",
                "message": "describe",
                "model": "vision",
                "images": [image] * 10,
            },
        )
    assert response.status_code == 200
    assert len(runner.calls[0][2]["images"]) == 10
