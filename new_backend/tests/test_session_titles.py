"""Automatic naming through public workspace APIs and the external model boundary."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


class ReplyPi:
    def __init__(self, *, blocked=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.prompts = []
        if not blocked:
            self.release.set()

    async def prompt(self, session_id, message, on_event, **kwargs):
        self.prompts.append(message)
        self.started.set()
        await self.release.wait()
        await on_event({"type": "message_end", "message": {
            "role": "assistant", "usage": {"totalTokens": 30},
        }})
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "先检查蛋白质与 RNA 的接触残基。"}


class TitleGateway:
    def __init__(self, *, blocked=False, status=200, title="蛋白质与 RNA 结合分析"):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = []
        self.status = status
        self.title = title
        if not blocked:
            self.release.set()

    async def handle(self, request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "chat-large"}, {"id": "title-small"}]})
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": []})
        assert request.url.path == "/v1/chat/completions"
        self.requests.append((json.loads(request.content), dict(request.headers)))
        self.started.set()
        await self.release.wait()
        return httpx.Response(self.status, json={
            "choices": [{"message": {"content": self.title}}],
            "usage": {"total_tokens": 13},
        })


def application(tmp_path, runner, gateway, **settings):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        model_gateway_base_url="https://gateway.invalid", model_gateway_api_key="test-only-key",
        model_gateway_model="chat-large", model_gateway_kind="litellm", **settings,
    ), pi_runner=runner)
    app.state.model_catalog.transport = httpx.MockTransport(gateway.handle)
    return app


async def login(client, email="alice@example.org"):
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def ready(client, path, headers, field, expected):
    for _ in range(200):
        response = await client.get(path, headers=headers)
        data = response.json()
        if data.get(field) == expected:
            return data
        await asyncio.sleep(0.01)
    pytest.fail(f"{path} did not reach {field}={expected}: {data}")


@pytest.mark.asyncio
async def test_title_is_generated_only_after_first_reply_without_delaying_it_and_is_charged_once(tmp_path):
    runner, gateway = ReplyPi(blocked=True), TitleGateway(blocked=True)
    app = application(tmp_path, runner, gateway)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "临时标题", "auto_title": True,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        run = (await client.post(f"{path}/messages", headers=headers, json={
            "content": "分析蛋白质和 RNA 的结合。", "model": "chat-large",
        })).json()
        await asyncio.wait_for(runner.started.wait(), 2)
        assert gateway.requests == []
        assert (await client.get(path, headers=headers)).json()["title"] == "临时标题"
        runner.release.set()
        await ready(client, f"/api/v1/runs/{run['run_id']}", headers, "status", "completed")
        messages = (await client.get(f"{path}/messages", headers=headers)).json()
        assert [message["role"] for message in messages] == ["user", "assistant"]
        await asyncio.wait_for(gateway.started.wait(), 2)
        assert (await client.get(path, headers=headers)).json()["title_status"] == "pending"
        gateway.release.set()
        named = await ready(client, path, headers, "title_status", "generated")
        assert named["title"] == "蛋白质与 RNA 结合分析"
        listing = (await client.get("/api/v1/c", headers=headers)).json()
        assert listing[0]["title"] == named["title"]
        body, upstream_headers = gateway.requests[0]
        assert body["model"] == "chat-large"
        assert body["stream"] is False and body["max_tokens"] <= 128
        assert "reasoning_effort" not in body and "tools" not in body
        assert "分析蛋白质和 RNA 的结合。" in body["messages"][1]["content"]
        assert "先检查蛋白质与 RNA 的接触残基。" in body["messages"][1]["content"]
        assert upstream_headers["x-litellm-end-user-id"] == body["user"]
        used = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"]
        assert used == 43
        second = (await client.post(f"{path}/messages", headers=headers, json={
            "content": "继续分析", "model": "chat-large",
        })).json()
        await ready(client, f"/api/v1/runs/{second['run_id']}", headers, "status", "completed")
        await asyncio.sleep(0.15)
        assert len(gateway.requests) == 1 and len(runner.prompts) == 2
        assert (await client.get(path, headers=headers)).json()["title"] == named["title"]


@pytest.mark.asyncio
async def test_manual_rename_wins_over_an_inflight_generated_title(tmp_path):
    runner, gateway = ReplyPi(), TitleGateway(blocked=True)
    app = application(tmp_path, runner, gateway)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "临时标题", "auto_title": True,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})
        await asyncio.wait_for(gateway.started.wait(), 2)
        response = await client.patch(path, headers=headers, json={"title": "我自己的标题"})
        assert response.status_code == 200
        gateway.release.set()
        await asyncio.sleep(0.15)
        stored = (await client.get(path, headers=headers)).json()
        assert stored["title"] == "我自己的标题" and stored["title_status"] == "manual"
        assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == 43


@pytest.mark.asyncio
async def test_configured_small_model_names_chat_without_changing_chat_model(tmp_path):
    runner, gateway = ReplyPi(), TitleGateway(title='"RNA 结合位点"')
    app = application(tmp_path, runner, gateway, title_model="title-small")
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        project = (await client.post("/api/v1/g", headers=headers, json={"name": "课题"})).json()
        path = f"/api/v1/g/g-p-{project['id'].removeprefix('project-')}/c"
        session = (await client.post(path, headers=headers, json={
            "title": "分析", "auto_title": True,
        })).json()
        path += f"/{session['id']}"
        await client.post(f"{path}/messages", headers=headers, json={"content": "找 RNA 结合位点", "model": "chat-large"})
        named = await ready(client, path, headers, "title_status", "generated")
        assert named["title"] == "RNA 结合位点"
        assert gateway.requests[0][0]["model"] == "title-small"
        other = await login(client, "bob@example.org")
        assert (await client.get(path, headers=other)).status_code == 404
        assert (await client.get("/api/v1/c", headers=other)).json() == []


@pytest.mark.parametrize("status,title", [(429, "ignored"), (200, "\n"), (200, "标题一\n解释一")])
@pytest.mark.asyncio
async def test_naming_failures_preserve_reply_and_temporary_title_without_retry(tmp_path, status, title):
    runner, gateway = ReplyPi(), TitleGateway(status=status, title=title)
    app = application(tmp_path, runner, gateway)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "临时标题", "auto_title": True,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        run = (await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})).json()
        stored = await ready(client, path, headers, "title_status", "failed")
        assert stored["title"] == "临时标题"
        assert (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()["status"] == "completed"
        assert len((await client.get(f"{path}/messages", headers=headers)).json()) == 2
        await asyncio.sleep(0.2)
        assert len(gateway.requests) == 1
        assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == (30 if status == 429 else 43)


@pytest.mark.asyncio
async def test_exhausted_quota_skips_the_extra_call_and_keeps_completed_reply(tmp_path):
    runner, gateway = ReplyPi(), TitleGateway()
    app = application(tmp_path, runner, gateway, member_monthly_token_limit=30)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "临时标题", "auto_title": True,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        await client.post(f"{path}/messages", headers=headers, json={"content": "hi"})
        stored = await ready(client, path, headers, "title_status", "failed")
        assert stored["status"] == "completed" and stored["title"] == "临时标题"
        assert gateway.requests == []
        assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == 30


@pytest.mark.asyncio
async def test_title_timeout_ends_auxiliary_job_without_retry_or_failing_reply(tmp_path):
    runner, gateway = ReplyPi(), TitleGateway(blocked=True)
    app = application(tmp_path, runner, gateway, title_timeout_seconds=1)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "临时标题", "auto_title": True,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})
        stored = await ready(client, path, headers, "title_status", "failed")
        assert stored["title"] == "临时标题" and stored["status"] == "completed"
        assert len((await client.get(f"{path}/messages", headers=headers)).json()) == 2
        # Unknown provider usage retains its hold, because timeout does not prove zero cost.
        assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] > 30
        await asyncio.sleep(0.2)
        assert len(gateway.requests) == 1


@pytest.mark.parametrize("rename,auto_title", [(True, True), (False, False)])
@pytest.mark.asyncio
async def test_manual_titles_do_not_trigger_automatic_naming(tmp_path, rename, auto_title):
    runner, gateway = ReplyPi(blocked=True), TitleGateway()
    app = application(tmp_path, runner, gateway)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={
            "title": "我的标题", "auto_title": auto_title,
        })).json()
        path = f"/api/v1/c/{session['id']}"
        run = (await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})).json()
        await asyncio.wait_for(runner.started.wait(), 2)
        if rename:
            await client.patch(path, headers=headers, json={"title": "我的标题"})
        runner.release.set()
        await ready(client, f"/api/v1/runs/{run['run_id']}", headers, "status", "completed")
        await asyncio.sleep(0.15)
        assert gateway.requests == []
        assert (await client.get(path, headers=headers)).json()["title"] == "我的标题"


@pytest.mark.asyncio
async def test_pending_title_survives_a_restart_and_generated_titles_are_not_regenerated(tmp_path):
    gateway = TitleGateway()
    first = application(tmp_path, ReplyPi(), gateway)
    # The turn can finish before the separate naming worker starts.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={"title": "临时", "auto_title": True})).json()
        path = f"/api/v1/c/{session['id']}"
        run = (await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})).json()
        await ready(client, f"/api/v1/runs/{run['run_id']}", headers, "status", "completed")
        assert (await client.get(path, headers=headers)).json()["title_status"] == "pending"
        assert gateway.requests == []
    await first.state.agent_service.stop()
    for index in range(2):
        app = application(tmp_path, ReplyPi(), gateway)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            headers = await login(client)
            await ready(client, path, headers, "title_status", "generated")
            await asyncio.sleep(0.15)
            assert len(gateway.requests) == 1
            assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == 43


@pytest.mark.asyncio
async def test_cancelled_partial_reply_is_not_used_for_naming(tmp_path):
    runner, gateway = ReplyPi(blocked=True), TitleGateway()
    app = application(tmp_path, runner, gateway)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await login(client)
        session = (await client.post("/api/v1/c", headers=headers, json={"title": "临时", "auto_title": True})).json()
        path = f"/api/v1/c/{session['id']}"
        run = (await client.post(f"{path}/messages", headers=headers, json={"content": "分析结构"})).json()
        await asyncio.wait_for(runner.started.wait(), 2)
        await client.delete(f"/api/v1/runs/{run['run_id']}", headers=headers)
        await asyncio.sleep(0.15)
        assert gateway.requests == []
        assert (await client.get(path, headers=headers)).json()["title"] == "临时"
