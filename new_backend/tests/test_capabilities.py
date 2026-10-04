import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


async def _login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_mcp_tool_catalog_and_mock_invoke_are_deterministic():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        catalog = await client.get("/api/v1/mcp/tools", headers=headers)
        invoked = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke",
            headers=headers,
            json={"query": "protein RNA complex"},
        )

    assert catalog.status_code == invoked.status_code == 200
    assert any(tool["name"] == "search_pdb" for tool in catalog.json())
    assert invoked.json()["status"] == "completed"
    assert invoked.json()["result"]["hits"][0]["pdb_id"] == "1A9N"


@pytest.mark.asyncio
async def test_mcp_mock_rejects_arguments_outside_published_schema_without_recording_run():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        missing = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers, json={},
        )
        wrong_type = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
            json={"query": 123},
        )
        extra = await client.post(
            "/api/v1/mcp/tools/search_pdb/invoke", headers=headers,
            json={"query": "RNA", "unexpected": True},
        )
        unknown = await client.post(
            "/api/v1/mcp/tools/unknown/invoke", headers=headers, json={},
        )
        runs = await client.get("/api/v1/tool-runs", headers=headers)

    for invalid in (missing, wrong_type, extra):
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert unknown.status_code == 404
    assert runs.json() == []


@pytest.mark.asyncio
async def test_af3_job_reserves_then_settles_daily_gpu_minutes():
    app = create_app(Settings(mock_af3_seconds=0.1))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, "alice@example.org")
            created = await client.post(
                "/api/v1/af3/jobs", headers=headers, json={"estimated_gpu_minutes": 20}
            )
            assert created.status_code == 200
            assert created.json()["status"] == "queued"
            during = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]
            assert during["used"] == 0
            assert during["reserved"] == 20
            assert during["remaining"] == 40

            job_id = created.json()["id"]
            for _ in range(50):
                finished = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=headers)
                if finished.json()["status"] == "completed":
                    break
                await asyncio.sleep(0.02)
            after = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]

    assert finished.json()["status"] == "completed"
    assert after["used"] == 18
    assert after["reserved"] == 0
    assert after["remaining"] == 42


@pytest.mark.asyncio
async def test_mock_job_completes_without_read_requests_advancing_it():
    app = create_app(Settings(mock_af3_seconds=0.1))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, "alice@example.org")
            created = await client.post("/api/v1/af3/jobs", headers=headers, json={})
            job_id = created.json()["id"]
            first = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=headers)
            second = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=headers)
            assert first.json()["status"] == second.json()["status"] == "queued"
            await asyncio.sleep(0.3)
            finished = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=headers)
            usage = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]

    assert finished.json()["status"] == "completed"
    assert usage["reserved"] == 0


@pytest.mark.asyncio
async def test_af3_quota_rejection_does_not_reserve_gpu():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        reserved = await client.post(
            "/api/v1/af3/jobs", headers=headers, json={"estimated_gpu_minutes": 50}
        )
        assert reserved.status_code == 200
        before = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]
        rejected = await client.post(
            "/api/v1/af3/jobs", headers=headers, json={"estimated_gpu_minutes": 20}
        )
        after = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "GPU_DAILY_QUOTA_EXCEEDED"
    assert after == before


@pytest.mark.asyncio
async def test_af3_job_is_private_and_wakes_waiting_run():
    app = create_app(Settings(mock_af3_seconds=0.1))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            alice = await _login(client, "alice@example.org")
            bob = await _login(client, "bob@example.org")
            (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
            session_id = (
                await client.post("/api/v1/c", headers=alice, json={"title": "AF3 分析"})
            ).json()["id"]
            run_id = (
                await client.post(
                    f"/api/v1/c/{session_id}/messages",
                    headers=alice,
                    json={"content": "请运行 AF3 预测"},
                )
            ).json()["run_id"]
            initial = _events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=alice)).text)
            queued = next(event for event in initial if event["type"] == "task.updated")
            assert queued["data"]["label"] == "AlphaFold 3"
            job_id = queued["data"]["job_id"]
            waiting_session = (
                await client.get("/api/v1/c", headers=alice)
            ).json()[0]
            assert waiting_session["latest_run_id"] == run_id
            assert waiting_session["status"] == "waiting"
            foreign = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=bob)
            for _ in range(50):
                final = _events(
                    (
                        await client.get(
                            f"/api/v1/runs/{run_id}/events?follow=false&after={queued['id']}", headers=alice
                        )
                    ).text
                )
                if final and final[-1]["type"] == "run.completed":
                    break
                await asyncio.sleep(0.02)
            completed_session = (
                await client.get("/api/v1/c", headers=alice)
            ).json()[0]

    assert foreign.status_code == 404
    assert any(event["type"] == "artifact.created" for event in final)
    assert final[-1]["type"] == "run.completed"
    assert completed_session["latest_run_id"] == run_id
    assert completed_session["status"] == "completed"


@pytest.mark.asyncio
async def test_af3_chat_rejects_insufficient_quota_without_creating_message():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = await _login(client, "alice@example.org")
        (await client.get("/api/v1/g", headers=headers)).json()[0]
        session = (
            await client.post("/api/v1/c", headers=headers, json={"title": "GPU 额度"})
        ).json()
        first = await client.post(
            "/api/v1/af3/jobs", headers=headers, json={"estimated_gpu_minutes": 41}
        )
        assert first.status_code == 200
        before = (await client.get("/api/v1/usage", headers=headers)).json()
        rejected = await client.post(
            f"/api/v1/c/{session['id']}/messages",
            headers=headers,
            json={"content": "Run AF3"},
        )
        after = (await client.get("/api/v1/usage", headers=headers)).json()
        messages = (await client.get(f"/api/v1/c/{session['id']}/messages", headers=headers)).json()

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "GPU_DAILY_QUOTA_EXCEEDED"
    assert after == before
    assert messages == []
