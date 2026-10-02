import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


async def _login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_user_can_send_message_in_own_project_session():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        projects = await client.get("/api/v1/g", headers=headers)
        assert projects.status_code == 200
        projects.json()[0]["id"]
        sessions = await client.get("/api/v1/c", headers=headers)
        assert sessions.status_code == 200
        session_id = sessions.json()[0]["id"]

        sent = await client.post(
            f"/api/v1/c/{session_id}/messages",
            headers=headers,
            json={"content": "请检索 PDB", "attachments": [], "skills": [], "resources": []},
        )
        messages = await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)

    assert sent.status_code == 200
    assert sent.json()["run_id"]
    assert messages.status_code == 200
    assert any(
        message["parts"] == [{"type": "text", "text": "请检索 PDB"}] for message in messages.json()
    )


@pytest.mark.asyncio
async def test_foreign_session_is_not_visible():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        alice = await _login(client, "alice@example.org")
        bob = await _login(client, "bob@example.org")
        (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
        session_id = (
            await client.get("/api/v1/c", headers=alice)
        ).json()[0]["id"]

        response = await client.get(f"/api/v1/c/{session_id}/messages", headers=bob)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_run_events_resume_after_cursor_without_duplicates():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = (
            await client.get("/api/v1/c", headers=headers)
        ).json()[0]["id"]
        run_id = (
            await client.post(
                f"/api/v1/c/{session_id}/messages",
                headers=headers,
                json={"content": "你好", "attachments": [], "skills": [], "resources": []},
            )
        ).json()["run_id"]

        first = await client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
        resumed = await client.get(f"/api/v1/runs/{run_id}/events?after=1", headers=headers)

    original_events = _events(first.text)
    resumed_events = _events(resumed.text)
    assert first.status_code == resumed.status_code == 200
    assert [event["id"] for event in original_events] == ["1", "2"]
    assert [event["id"] for event in resumed_events] == ["2"]
    assert all(event["run_id"] == run_id for event in resumed_events)


@pytest.mark.asyncio
async def test_foreign_run_events_are_not_visible():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        alice = await _login(client, "alice@example.org")
        bob = await _login(client, "bob@example.org")
        (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
        session_id = (
            await client.get("/api/v1/c", headers=alice)
        ).json()[0]["id"]
        run_id = (
            await client.post(
                f"/api/v1/c/{session_id}/messages", headers=alice, json={"content": "你好"}
            )
        ).json()["run_id"]

        response = await client.get(f"/api/v1/runs/{run_id}/events", headers=bob)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_event_stream_waits_for_a_background_task_and_emits_completion():
    app = create_app(Settings(mock_af3_seconds=0.1))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before HTTP requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, "alice@example.org")
            (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = (await client.get(
                "/api/v1/c", headers=headers
            )).json()[0]["id"]
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "run AF3"},
            )).json()["run_id"]
            response = await asyncio.wait_for(
                client.get(f"/api/v1/runs/{run_id}/events", headers=headers), timeout=2
            )

    assert response.status_code == 200
    assert "run.completed" in [event["type"] for event in _events(response.text)]


@pytest.mark.asyncio
async def test_run_event_snapshot_returns_while_background_task_is_waiting():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client, "alice@example.org")
        (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = (await client.get(
            "/api/v1/c", headers=headers
        )).json()[0]["id"]
        run_id = (await client.post(
            f"/api/v1/c/{session_id}/messages", headers=headers,
            json={"content": "run AF3"},
        )).json()["run_id"]
        response = await asyncio.wait_for(
            client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers),
            timeout=0.5,
        )

    assert response.status_code == 200
    assert "run.completed" not in [event["type"] for event in _events(response.text)]
