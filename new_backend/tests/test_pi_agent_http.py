import asyncio
import json

import httpx
import pytest

from app.adapters.live.pi_rpc import PiRpcError
from app.config import Settings
from app.main import create_app


class FakePi:
    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    async def prompt(self, session_id: str, message: str, on_event, **kwargs):
        self.prompts.append((session_id, message))
        await on_event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Pi 回复"}})
        await on_event({"type": "agent_settled"})
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Pi 回复"}


@pytest.mark.asyncio
async def test_initial_pi_run_retries_transient_process_failure_without_duplicate_message(tmp_path):
    class FlakyPi:
        attempts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                await on_event({"type": "message_update", "assistantMessageEvent": {
                    "type": "text_delta", "delta": "unfinished",
                }})
                raise PiRpcError("Pi exited before settling", retryable=True)
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    runner = FlakyPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              resume_retry_seconds=0.01), pi_runner=runner)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = project_id.replace("project-", "session-")
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "Analyze"})).json()["run_id"]
            for _ in range(150):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            messages = (await client.get(f"/api/v1/c/{session_id}/messages",
                                         headers=headers)).json()
            run_events = events((await client.get(
                f"/api/v1/runs/{run_id}/events?follow=false", headers=headers,
            )).text)

    assert status == "completed"
    assert runner.attempts == 2
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert [event["type"] for event in run_events] == [
        "message.delta", "run.retrying", "run.completed",
    ]
    assert next(event for event in run_events if event["type"] == "run.retrying")["data"]["reset_message"] is True


@pytest.mark.parametrize(("emit_tool", "code", "expected_attempts"), [
    (False, "MODEL_GATEWAY_RATE_LIMITED", 1),
    (True, "PI_RUN_FAILED", 1),
    (False, "PI_RUN_FAILED", 3),
])
@pytest.mark.asyncio
async def test_initial_pi_retry_stops_for_provider_limits_side_effects_and_exhaustion(
    tmp_path, emit_tool: bool, code: str, expected_attempts: int,
):
    class FailingPi:
        attempts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.attempts += 1
            if emit_tool:
                await on_event({
                    "type": "tool_execution_start", "toolCallId": "call-1",
                    "toolName": "external_write",
                })
            raise PiRpcError("upstream failure", code=code, retryable=True)

    runner = FailingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              resume_retry_seconds=0.01), pi_runner=runner)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = project_id.replace("project-", "session-")
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "Analyze"})).json()["run_id"]
            for _ in range(150):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
            run_events = events((await client.get(
                f"/api/v1/runs/{run_id}/events?follow=false", headers=headers,
            )).text)
            messages = (await client.get(f"/api/v1/c/{session_id}/messages",
                                         headers=headers)).json()

    assert status == "failed"
    assert runner.attempts == expected_attempts
    assert [message["role"] for message in messages] == ["user"]
    assert len([event for event in run_events if event["type"] == "run.retrying"]) == expected_attempts - 1
    assert [event for event in run_events if event["type"] == "run.failed"][0]["data"]["code"] == code


@pytest.mark.parametrize(("content", "reported_tokens"), [("hello", 17), ("x" * 40, 3)])
@pytest.mark.asyncio
async def test_pi_token_usage_reconciles_with_reported_model_tokens(
    tmp_path, content: str, reported_tokens: int
):
    class UsagePi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await on_event({
                "type": "message_end",
                "message": {"role": "assistant", "usage": {"totalTokens": reported_tokens}},
            })
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=UsagePi(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        project = (await client.get("/api/v1/g", headers=headers)).json()[0]
        session_id = f"session-{project['id'].removeprefix('project-')}"
        sent = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": content}
        )
        run_id = sent.json()["run_id"]
        for _ in range(100):
            run = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
            if run.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        usage = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]
    assert usage["used"] == reported_tokens
    assert usage["remaining"] == usage["limit"] - reported_tokens


@pytest.mark.asyncio
async def test_pi_model_retry_is_visible_without_exposing_provider_error(tmp_path):
    class RetryPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await on_event({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3,
                            "delayMs": 2000, "errorMessage": "429 secret-provider-detail"})
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=RetryPi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        run_id = (await client.post(f"/api/v1/c/{session_id}/messages", headers=headers,
                                    json={"content": "你好"})).json()["run_id"]
        for _ in range(100):
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                               headers=headers)).text)
            if run_events and run_events[-1]["type"] == "run.completed":
                break
            await asyncio.sleep(0.01)

    retry = next(event for event in run_events if event["type"] == "run.retrying")
    assert retry["data"] == {"attempt": 1, "max_attempts": 3, "delay_ms": 2000,
                             "reset_message": False}
    assert "secret-provider-detail" not in json.dumps(run_events)


@pytest.mark.asyncio
async def test_each_pi_model_attempt_is_auditable_without_double_charging_tokens(tmp_path):
    class RetriedPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "error", "usage": {"totalTokens": 7},
            }})
            await on_event({"type": "auto_retry_start", "attempt": 1,
                            "maxAttempts": 3, "delayMs": 2000})
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 11},
            }})
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=RetriedPi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        run_id = (await client.post(f"/api/v1/c/{session_id}/messages", headers=headers,
                                    json={"content": "Analyze"})).json()["run_id"]
        for _ in range(100):
            status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
            if status == "completed":
                break
            await asyncio.sleep(0.01)
        entries = (await client.get("/api/v1/usage/entries", headers=headers)).json()
        used = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"]

    attempts = [entry for entry in entries if entry["kind"] == "model_attempt"]
    assert [(entry["amount"], entry["status"]) for entry in attempts] == [
        (7, "error"), (11, "completed"),
    ]
    assert all(entry["run_id"] == run_id for entry in attempts)
    assert used == 18


@pytest.mark.asyncio
async def test_reported_model_usage_is_charged_across_an_interrupted_pi_retry(tmp_path):
    first_reported = asyncio.Event()

    class InterruptedPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 17},
            }})
            first_reported.set()
            await asyncio.Event().wait()

    path = str(tmp_path / "agent.sqlite3")
    settings = Settings(agent_runtime="pi", agent_db_path=path, resume_retry_seconds=0.01)
    first_app = create_app(settings, pi_runner=InterruptedPi())
    async with first_app.router.lifespan_context(first_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url="http://test",
        ) as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "Analyze"},
            )).json()["run_id"]
            await asyncio.wait_for(first_reported.wait(), timeout=2)

    class RetriedPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 3},
            }})
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    second_app = create_app(settings, pi_runner=RetriedPi())
    async with second_app.router.lifespan_context(second_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="http://test",
        ) as client:
            headers = await login(client)
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.02)
            assert status == "completed"
            entries = (await client.get("/api/v1/usage/entries", headers=headers)).json()
            used = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"]

    assert [entry["amount"] for entry in entries if entry["kind"] == "model_attempt"] == [17, 3]
    assert used == 20


@pytest.mark.asyncio
async def test_interrupted_af3_wakeup_releases_unused_token_reservation_before_retry(tmp_path):
    first_resume_reported = asyncio.Event()

    class InterruptedResumePi(ToolCallingFakePi):
        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                await on_event({"type": "message_end", "message": {
                    "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 4},
                }})
                first_resume_reported.set()
                await asyncio.Event().wait()
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 2},
            }})
            return await super().prompt(session_id, message, on_event, **kwargs)

    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        mock_af3_seconds=0.05, resume_retry_seconds=0.01,
    )
    first_runner = InterruptedResumePi()
    first_app = create_app(settings, pi_runner=first_runner)
    first_runner.app = first_app
    async with first_app.router.lifespan_context(first_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url="http://test",
        ) as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "Analyze with AF3"},
            )).json()["run_id"]
            await asyncio.wait_for(first_resume_reported.wait(), timeout=3)

    class RetriedResumePi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            assert message.startswith("/pskit_resume ")
            await on_event({"type": "message_end", "message": {
                "role": "assistant", "stopReason": "end", "usage": {"totalTokens": 3},
            }})
            return {"session_file": f"/tmp/{session_id}-resumed.jsonl", "text": "完成"}

    second_app = create_app(settings, pi_runner=RetriedResumePi())
    async with second_app.router.lifespan_context(second_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="http://test",
        ) as client:
            headers = await login(client)
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.02)
            assert status == "completed"
            entries = (await client.get("/api/v1/usage/entries", headers=headers)).json()
            used = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"]

    assert [entry["amount"] for entry in entries if entry["kind"] == "model_attempt"] == [2, 4, 3]
    assert used == 9


@pytest.mark.asyncio
async def test_high_cost_af3_waits_for_explicit_approval_then_resumes_pi(tmp_path):
    class ApprovalPi:
        def __init__(self):
            self.app = None
            self.approval_id = None
            self.prompts = []

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.prompts.append(message)
            if message.startswith("/pskit_resume "):
                return {"session_file": kwargs["session_file"], "text": "已分析结果"}
            env = kwargs["environment"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="http://test") as client:
                response = await client.post("/internal/af3/jobs", headers={
                    "Authorization": f"Bearer {env['PSKIT_AGENT_TOOL_TOKEN']}",
                }, json={"run_id": env["PSKIT_RUN_ID"], "tool_call_id": "call-40",
                         "estimated_gpu_minutes": 40})
            assert response.status_code == 200, response.text
            self.approval_id = response.json()["approval_id"]
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": ""}

    runner = ApprovalPi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "approval.sqlite3"),
        af3_approval_threshold=30, af3_executor="callback", compute_callback_key="compute-secret",
        af3_min_gpu_memory_mb=40_960,
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler must be running.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = await login(client)
            bob = await login(client, "bob@example.org")
            direct = await client.post("/api/v1/af3/jobs", headers=alice,
                                       json={"estimated_gpu_minutes": 40})
            assert direct.status_code == 403
            project_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=alice, json={"content": "运行大规模 AF3"})).json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=alice)).json()["status"]
                if status == "waiting" and runner.approval_id:
                    break
                await asyncio.sleep(0.01)
            assert status == "waiting"
            before = (await client.get("/api/v1/usage", headers=alice)).json()["gpu"]
            unauthorized = await client.post(
                f"/api/v1/runs/{run_id}/approvals/{runner.approval_id}",
                headers=bob, json={"decision": "approved"},
            )
            approved = await client.post(
                f"/api/v1/runs/{run_id}/approvals/{runner.approval_id}",
                headers=alice, json={"decision": "approved"},
            )
            repeated = await client.post(
                f"/api/v1/runs/{run_id}/approvals/{runner.approval_id}",
                headers=alice, json={"decision": "approved"},
            )
            job_id = approved.json()["job_id"]
            approved_job = await client.get(f"/api/v1/af3/jobs/{job_id}", headers=alice)
            during = (await client.get("/api/v1/usage", headers=alice)).json()["gpu"]
            await client.post(f"/internal/af3/jobs/{job_id}/result",
                              headers={"X-Compute-Key": "compute-secret"}, json={
                "status": "completed", "actual_gpu_minutes": 32,
                "artifacts": [{"id": "structure-1", "name": "model.cif", "kind": "structure"}],
            })
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=alice)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                           headers=alice)).text)

    assert unauthorized.status_code == 404
    assert approved.status_code == repeated.status_code == 200
    assert approved.json()["job_id"] == repeated.json()["job_id"]
    assert approved_job.json()["resource_requirements"]["min_gpu_memory_mb"] == 40_960
    assert before["reserved"] == 0 and during["reserved"] == 40
    assert status == "completed"
    assert len([event for event in run_events if event["type"] == "approval.required"]) == 1
    assert len([event for event in run_events if event["type"] == "approval.resolved"]) == 1
    assert len([message for message in runner.prompts if message.startswith("/pskit_resume ")]) == 1


async def login(client: httpx.AsyncClient, email: str = "alice@example.org") -> dict[str, str]:
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_internal_model_preflight_requires_run_token_and_caps_each_request(tmp_path):
    class BlockingPi:
        def __init__(self):
            self.started = asyncio.Event()

        async def prompt(self, _session_id, _message, _on_event, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()

    runner = BlockingPi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=runner,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = project_id.replace("project-", "session-")
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "Research"})).json()["run_id"]
            await runner.started.wait()
            owner = app.state.conversations.owner_for_run(run_id)
            app.state.conversations.set_token_limit(owner, 1000)
            payload = {"run_id": run_id, "call_id": "call-1", "prompt_bytes": 200,
                       "requested_output_tokens": 800}
            path = "/internal/model/calls/preflight"
            assert (await client.post(path, json=payload)).status_code == 401
            tool_headers = {"Authorization": f"Bearer {app.state.agent_service.tool_token(run_id)}"}
            accepted = await client.post(path, headers=tool_headers, json=payload)
            assert accepted.status_code == 200
            assert 1 <= accepted.json()["max_output_tokens"] <= 600
            replay = await client.post(path, headers=tool_headers, json=payload)
            assert replay.json() == accepted.json()
            denied = await client.post(path, headers=tool_headers, json={**payload,
                "call_id": "call-2", "prompt_bytes": 300})
            assert denied.status_code == 429
            assert denied.json()["detail"]["code"] == "TOKEN_QUOTA_EXCEEDED"


@pytest.mark.parametrize("gateway_kind", ["generic", "litellm"])
@pytest.mark.asyncio
async def test_model_proxy_keeps_upstream_key_server_side_and_releases_rejected_call(
    tmp_path, gateway_kind,
):
    class BlockingPi:
        def __init__(self):
            self.started = asyncio.Event()

        async def prompt(self, _session_id, _message, _on_event, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()

    received: list[httpx.Request] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        received.append(request)
        if len(received) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                                   'data: [DONE]\n\n')

    runner = BlockingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              model_gateway_base_url="https://gateway.example/v1",
                              model_gateway_model="research-model",
                              model_gateway_kind=gateway_kind), pi_runner=runner)
    app.state.agent_service.model_gateway_api_key = "server-secret"
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway_client:
        app.state.model_gateway_http_client = gateway_client
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = await login(client)
                project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
                session_id = project_id.replace("project-", "session-")
                run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                            headers=headers, json={"content": "Research"})).json()["run_id"]
                await runner.started.wait()
                owner = app.state.conversations.owner_for_run(run_id)
                app.state.conversations.set_token_limit(owner, 1000)
                token = app.state.agent_service.tool_token(run_id)
                body = {"model": "research-model", "messages": [{"role": "user", "content": "Hi"}],
                        "stream": True, "max_completion_tokens": 800, "user": "forged-user"}
                result = await client.post("/internal/model/v1/chat/completions",
                                           headers={"Authorization": f"Bearer {run_id}.{token}",
                                                    "x-litellm-end-user-id": "forged-header"},
                                           json=body)
                assert result.status_code == 429
                assert received[0].url == "https://gateway.example/v1/chat/completions"
                assert received[0].headers["authorization"] == "Bearer server-secret"
                forwarded = json.loads(received[0].content)
                assert forwarded["max_completion_tokens"] < 800
                if gateway_kind == "litellm":
                    assert forwarded["user"] == owner
                    assert received[0].headers["x-litellm-end-user-id"] == owner
                else:
                    assert "user" not in forwarded
                    assert "x-litellm-end-user-id" not in received[0].headers
                assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] < 100
                streamed = await client.post("/internal/model/v1/chat/completions",
                                             headers={"Authorization": f"Bearer {run_id}.{token}"},
                                             json=body)
                assert streamed.status_code == 200
                assert "data: [DONE]" in streamed.text
                app.state.conversations.record_model_attempt(owner, run_id, 15, "completed")
                app.state.conversations.settle_current_tokens(owner, run_id)
                assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == 15
                app.state.conversations.set_token_limit(owner, 15)
                depleted = await client.post("/internal/model/v1/chat/completions",
                                             headers={"Authorization": f"Bearer {run_id}.{token}"},
                                             json=body)
                assert depleted.status_code == 402
                assert depleted.json()["error"]["code"] == "PSKIT_TOKEN_QUOTA_EXCEEDED"
                assert len(received) == 2


def events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_pi_run_messages_and_event_cursor_survive_restart(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    settings = Settings(agent_runtime="pi", agent_db_path=str(db_path))
    fake = FakePi()
    app = create_app(settings, pi_runner=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        session_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"].replace("project-", "session-")
        sent = await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "你好"})
        assert sent.status_code == 200
        run_id = sent.json()["run_id"]
        for _ in range(100):
            first = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)).text)
            if first and first[-1]["type"] == "run.completed":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Pi run did not finish")

    restarted = create_app(settings, pi_runner=FakePi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
        headers = await login(client)
        messages = await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)
        (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        sessions = await client.get("/api/v1/c", headers=headers)
        resumed = await client.get(f"/api/v1/runs/{run_id}/events?follow=false&after=1", headers=headers)
        foreign = await login(client, "bob@example.org")
        denied = await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=foreign)

    assert [message["role"] for message in messages.json()] == ["user", "assistant"]
    assert messages.json()[-1]["parts"] == [{"type": "text", "text": "Pi 回复"}]
    assert sessions.json()[0]["latest_run_id"] == run_id
    assert sessions.json()[0]["status"] == "completed"
    assert [event["id"] for event in events(resumed.text)] == [event["id"] for event in first[1:]]
    assert denied.status_code == 404


class ToolCallingFakePi:
    def __init__(self) -> None:
        self.app = None
        self.job_id: str | None = None
        self.prompts: list[str] = []

    async def prompt(self, session_id: str, message: str, on_event, **kwargs):
        self.prompts.append(message)
        if message.startswith("/pskit_resume "):
            environment = kwargs["environment"]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                checked = await client.get(
                    f"/internal/af3/jobs/{message.removeprefix('/pskit_resume ')}",
                    params={"run_id": environment["PSKIT_RUN_ID"]},
                    headers={"Authorization": f"Bearer {environment['PSKIT_AGENT_TOOL_TOKEN']}"},
                )
                assert checked.status_code == 200, checked.text
                assert checked.json()["status"] == "completed"
            await on_event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "结构已分析"}})
            await on_event({"type": "agent_settled"})
            return {"session_file": f"/tmp/{session_id}-resumed.jsonl", "text": "结构已分析"}
        environment = kwargs["environment"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            submitted = await client.post(
                "/internal/af3/jobs",
                headers={"Authorization": f"Bearer {environment['PSKIT_AGENT_TOOL_TOKEN']}"},
                json={
                    "run_id": environment["PSKIT_RUN_ID"],
                    "tool_call_id": "call-af3-1",
                    "estimated_gpu_minutes": 20,
                },
            )
            assert submitted.status_code == 200, submitted.text
            duplicate = await client.post(
                "/internal/af3/jobs",
                headers={"Authorization": f"Bearer {environment['PSKIT_AGENT_TOOL_TOKEN']}"},
                json={
                    "run_id": environment["PSKIT_RUN_ID"],
                    "tool_call_id": "call-af3-1",
                    "estimated_gpu_minutes": 20,
                },
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["id"] == submitted.json()["id"]
            self.job_id = submitted.json()["id"]
        await on_event({"type": "agent_settled"})
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": ""}


@pytest.mark.asyncio
async def test_pi_tool_submission_is_private_idempotent_and_reserves_gpu(tmp_path):
    runner = ToolCallingFakePi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=runner,
    )
    runner.app = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = await login(client)
        bob = await login(client, "bob@example.org")
        project_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        sent = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=alice, json={"content": "运行 AF3"}
        )
        assert sent.status_code == 200
        run_id = sent.json()["run_id"]
        for _ in range(100):
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=alice)).text)
            if runner.job_id and any(event["type"] == "task.updated" for event in run_events):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Pi tool did not submit a task")
        assert run_events[-1]["type"] != "run.completed"
        job = await client.get(f"/api/v1/af3/jobs/{runner.job_id}", headers=alice)
        foreign = await client.get(f"/api/v1/af3/jobs/{runner.job_id}", headers=bob)
        usage = (await client.get("/api/v1/usage", headers=alice)).json()

    assert job.status_code == 200 and job.json()["status"] == "queued"
    assert foreign.status_code == 404
    assert usage["gpu"]["reserved"] == 20


@pytest.mark.asyncio
async def test_af3_mock_finishes_and_wakes_pi_without_browser_job_polling(tmp_path):
    runner = ToolCallingFakePi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"), mock_af3_seconds=0.1),
        pi_runner=runner,
    )
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start lifespan before ASGI requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            user_id = (await client.get("/api/v1/me", headers=headers)).json()["id"]
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "运行 AF3"}
            )
            run_id = sent.json()["run_id"]
            for _ in range(200):
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)).text)
                if run_events and run_events[-1]["type"] == "run.completed":
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Background AF3 did not wake Pi")
            messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=headers)).json()
            usage = (await client.get("/api/v1/usage", headers=headers)).json()

    assert any(event["type"] == "artifact.created" for event in run_events)
    assert messages[-1]["parts"][0] == {"type": "text", "text": "结构已分析"}
    assert any(part["type"] == "artifact" and part["name"] == "af3_prediction.cif"
               for part in messages[-1]["parts"])
    assert any(part["type"] == "tool_result" and part["tool"] == "submit_af3"
               and part["result"]["task_id"] == runner.job_id
               and part["result"]["status"] == "completed"
               and part["result"]["simulation"] is True
               for part in messages[-1]["parts"])
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert usage["gpu"]["used"] == 18 and usage["gpu"]["reserved"] == 0
    assert app.state.conversations.session_file_for(user_id, session_id) == (
        f"/tmp/{session_id}-resumed.jsonl"
    )


@pytest.mark.asyncio
async def test_chained_af3_jobs_wake_pi_twice_even_if_second_finishes_early(tmp_path):
    class ChainedPi:
        def __init__(self):
            self.app = None
            self.resume_count = 0
            self.second_job_status_before_settle = None
            self.job_ids = []
            self.resume_job_ids = []
            self.resume_session_files = []
            self.pending_before_settle = None
            self.approval_before_settle = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                self.resume_count += 1
                self.resume_job_ids.append(message.removeprefix("/pskit_resume "))
                self.resume_session_files.append(kwargs["session_file"])
                if self.resume_count == 2:
                    return {"session_file": f"/tmp/{session_id}-final.jsonl", "text": "两个任务已完成"}
            tool_number = self.resume_count + 1
            env = kwargs["environment"]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test",
            ) as client:
                submitted = await client.post(
                    "/internal/af3/jobs",
                    headers={"Authorization": f"Bearer {env['PSKIT_AGENT_TOOL_TOKEN']}"},
                    json={"run_id": env["PSKIT_RUN_ID"],
                          "tool_call_id": f"call-{tool_number}", "estimated_gpu_minutes": 10},
                )
                assert submitted.status_code == 200, submitted.text
                self.job_ids.append(submitted.json()["id"])
            if self.resume_count == 1:
                await asyncio.sleep(0.25)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                ) as client:
                    checked = await client.get(
                        f"/internal/af3/jobs/{submitted.json()['id']}",
                        params={"run_id": env["PSKIT_RUN_ID"]},
                        headers={"Authorization": f"Bearer {env['PSKIT_AGENT_TOOL_TOKEN']}"},
                    )
                    self.second_job_status_before_settle = checked.json()["status"]
                    self.pending_before_settle = self.app.state.conversations.has_job_for_run(
                        env["PSKIT_RUN_ID"]
                    )
                    self.approval_before_settle = (
                        self.app.state.conversations.has_pending_approval_for_run(env["PSKIT_RUN_ID"])
                    )
            return {"session_file": f"/tmp/{session_id}-step-{tool_number}.jsonl", "text": ""}

    runner = ChainedPi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"), mock_af3_seconds=0.02,
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler before client.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            user_id = (await client.get("/api/v1/me", headers=headers)).json()["id"]
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "运行两个 AF3 任务"},
            )).json()["run_id"]
            for _ in range(200):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.02)
    assert status == "completed"
    assert runner.resume_count == 2
    assert runner.resume_job_ids == runner.job_ids
    assert runner.resume_session_files[1] == f"/tmp/{session_id}-step-2.jsonl"
    assert runner.second_job_status_before_settle == "completed"
    assert runner.pending_before_settle is False
    assert runner.approval_before_settle is False
    assert app.state.conversations.session_file_for(user_id, session_id) == (
        f"/tmp/{session_id}-final.jsonl"
    )


@pytest.mark.asyncio
async def test_af3_callback_completes_once_and_wakes_waiting_pi(tmp_path):
    runner = ToolCallingFakePi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-secret",
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler must be running.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "运行 AF3"})).json()["run_id"]
            for _ in range(100):
                if runner.job_id and (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"] == "waiting":
                    break
                await asyncio.sleep(0.01)
            assert runner.job_id
            await asyncio.sleep(0.1)
            assert (await client.get(f"/api/v1/af3/jobs/{runner.job_id}", headers=headers)).json()["status"] == "queued"
            denied = await client.post(f"/internal/af3/jobs/{runner.job_id}/result", json={
                "status": "completed", "actual_gpu_minutes": 12,
            })
            result = await client.post(f"/internal/af3/jobs/{runner.job_id}/result",
                                       headers={"X-Compute-Key": "compute-secret"}, json={
                "status": "completed", "actual_gpu_minutes": 12,
                "artifacts": [{"id": "structure-1", "name": "model.cif", "kind": "structure"}],
            })
            duplicate = await client.post(f"/internal/af3/jobs/{runner.job_id}/result",
                                          headers={"X-Compute-Key": "compute-secret"}, json={
                "status": "completed", "actual_gpu_minutes": 12,
            })
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            usage = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]
            events_after = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                              headers=headers)).text)

    assert denied.status_code == 404
    assert result.status_code == duplicate.status_code == 200
    assert status == "completed"
    assert result.json()["artifacts"][0]["name"] == "model.cif"
    assert usage["used"] == 12 and usage["reserved"] == 0
    assert len([event for event in events_after if event["type"] == "artifact.created"]) == 1
    assert len([message for message in runner.prompts if message.startswith("/pskit_resume ")]) == 1


@pytest.mark.asyncio
async def test_af3_callback_failure_releases_reservation_without_resuming_pi(tmp_path):
    runner = ToolCallingFakePi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-secret",
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler must be running.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "运行 AF3"})).json()["run_id"]
            for _ in range(100):
                if runner.job_id and (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"] == "waiting":
                    break
                await asyncio.sleep(0.01)
            assert runner.job_id
            failed = await client.post(f"/internal/af3/jobs/{runner.job_id}/result",
                                       headers={"X-Compute-Key": "compute-secret"}, json={
                "status": "failed", "actual_gpu_minutes": 7,
            })
            repeated = await client.post(f"/internal/af3/jobs/{runner.job_id}/result",
                                         headers={"X-Compute-Key": "compute-secret"}, json={
                "status": "completed", "actual_gpu_minutes": 12,
            })
            status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
            usage = (await client.get("/api/v1/usage", headers=headers)).json()["gpu"]
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                           headers=headers)).text)

    assert failed.status_code == repeated.status_code == 200
    assert repeated.json()["status"] == "failed"
    assert status == "failed"
    assert usage["reserved"] == 0 and usage["used"] == 7
    assert len([event for event in run_events if event["type"] == "run.failed"]) == 1


@pytest.mark.asyncio
async def test_af3_completion_before_pi_settles_still_wakes_the_run(tmp_path):
    class SlowSettlingPi(ToolCallingFakePi):
        async def prompt(self, session_id, message, on_event, **kwargs):
            result = await super().prompt(session_id, message, on_event, **kwargs)
            if not message.startswith("/pskit_resume "):
                await asyncio.sleep(0.2)
            return result

    runner = SlowSettlingPi()
    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"), mock_af3_seconds=0.02),
        pi_runner=runner,
    )
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start lifespan first.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (
                await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "运行 AF3"})
            ).json()["run_id"]
            for _ in range(200):
                status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
                if status.json()["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Run did not settle")
    assert status.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_waiting_af3_run_resumes_after_python_restart_once(tmp_path):
    class ContextRecordingPi(ToolCallingFakePi):
        resume_context = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                self.resume_context = kwargs
            return await super().prompt(session_id, message, on_event, **kwargs)

    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"), mock_af3_seconds=0.1
    )
    first_runner = ToolCallingFakePi()
    first_app = create_app(settings, pi_runner=first_runner)
    first_runner.app = first_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://test"
    ) as client:
        headers = await login(client)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        sent = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=headers,
            json={"content": "运行 AF3", "skills": [{"id": "structure-review", "name": "Structure"}]},
        )
        run_id = sent.json()["run_id"]
        for _ in range(100):
            status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
            if status.status_code == 200 and status.json()["status"] == "waiting":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Run did not suspend")

    second_runner = ContextRecordingPi()
    second_app = create_app(settings, pi_runner=second_runner)
    second_runner.app = second_app
    async with second_app.router.lifespan_context(second_app):  # noqa: SIM117 - Start lifespan first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="http://test"
        ) as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            sessions = await client.get("/api/v1/c", headers=headers)
            assert sessions.json()[0]["latest_run_id"] == run_id
            for _ in range(200):
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)).text)
                if run_events and run_events[-1]["type"] == "run.completed":
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Persisted task did not resume")
            job = await client.get(f"/api/v1/af3/jobs/{first_runner.job_id}", headers=headers)
            usage = (await client.get("/api/v1/usage", headers=headers)).json()
            status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)

    assert job.json()["status"] == "completed"
    assert usage["gpu"]["used"] == 18
    assert status.json()["status"] == "completed"
    assert len([event for event in run_events if event["type"] == "run.completed"]) == 1
    assert "Use the available PDB and UniProt capabilities" in second_runner.resume_context["system_prompt_suffix"]
    assert {tool["name"] for tool in json.loads(second_runner.resume_context["environment"]["PSKIT_MCP_TOOLS_JSON"])} == {"search_pdb", "fetch_uniprot"}


@pytest.mark.asyncio
async def test_pi_failure_is_visible_and_never_looks_completed(tmp_path):
    class FailingPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            raise RuntimeError("provider unavailable")

    app = create_app(
        Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=FailingPi(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        run_id = (
            await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "你好"})
        ).json()["run_id"]
        for _ in range(100):
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)).text)
            if run_events and run_events[-1]["type"] == "run.failed":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Failure event was not emitted")
        status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert status.json()["status"] == "failed"
    assert all(event["type"] != "run.completed" for event in run_events)


@pytest.mark.asyncio
async def test_model_gateway_budget_failure_has_stable_public_code_without_upstream_body(tmp_path):
    class ExhaustedPi:
        async def prompt(self, session_id, message, on_event, **kwargs):
            raise PiRpcError("secret gateway billing response",
                             code="MODEL_GATEWAY_QUOTA_EXHAUSTED")

    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=ExhaustedPi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
        session_id = f"session-{project_id.removeprefix('project-')}"
        run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                    headers=headers, json={"content": "你好"})).json()["run_id"]
        for _ in range(100):
            run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                               headers=headers)).text)
            if run_events and run_events[-1]["type"] == "run.failed":
                break
            await asyncio.sleep(0.01)
        usage = (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]
    failed = run_events[-1]
    assert failed["data"]["code"] == "MODEL_GATEWAY_QUOTA_EXHAUSTED"
    assert "secret gateway billing response" not in json.dumps(run_events)
    assert usage["used"] == 0


@pytest.mark.asyncio
async def test_failed_resume_retries_at_most_three_times_then_reports_failure(tmp_path):
    class FailingResumePi(ToolCallingFakePi):
        attempts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                self.attempts += 1
                return {"session_file": kwargs["session_file"], "text": ""}
            return await super().prompt(session_id, message, on_event, **kwargs)

    runner = FailingResumePi()
    app = create_app(
        Settings(
            agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
            mock_af3_seconds=0.1, resume_retry_seconds=0.02,
        ),
        pi_runner=runner,
    )
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start lifespan before ASGI requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (
                await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "运行 AF3"})
            ).json()["run_id"]
            for _ in range(200):
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false", headers=headers)).text)
                if run_events and run_events[-1]["type"] == "run.failed":
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Run did not report exhausted resume retries")
            status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)

    assert runner.attempts == 3
    assert status.json()["status"] == "failed"
    assert len([event for event in run_events if event["type"] == "run.failed"]) == 1


@pytest.mark.asyncio
async def test_resume_does_not_replay_after_a_new_tool_started(tmp_path):
    class ToolThenFailurePi(ToolCallingFakePi):
        attempts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                self.attempts += 1
                await on_event({
                    "type": "tool_execution_start", "toolCallId": "call-external",
                    "toolName": "external_write",
                })
                raise RuntimeError("unknown external outcome")
            return await super().prompt(session_id, message, on_event, **kwargs)

    runner = ToolThenFailurePi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        mock_af3_seconds=0.1, resume_retry_seconds=0.02,
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler required.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(
                f"/api/v1/c/{session_id}/messages", headers=headers,
                json={"content": "运行 AF3"},
            )).json()["run_id"]
            for _ in range(100):
                run_events = events((await client.get(
                    f"/api/v1/runs/{run_id}/events?follow=false", headers=headers,
                )).text)
                if run_events and run_events[-1]["type"] == "run.failed":
                    break
                await asyncio.sleep(0.02)
    assert runner.attempts == 1
    assert run_events[-1]["data"]["code"] == "PI_RESUME_UNSAFE_RETRY"


@pytest.mark.asyncio
async def test_exhausted_model_rate_limit_does_not_retry_entire_af3_resume(tmp_path):
    class RateLimitedResumePi(ToolCallingFakePi):
        attempts = 0

        async def prompt(self, session_id, message, on_event, **kwargs):
            if message.startswith("/pskit_resume "):
                self.attempts += 1
                raise PiRpcError("429 upstream secret", code="MODEL_GATEWAY_RATE_LIMITED")
            return await super().prompt(session_id, message, on_event, **kwargs)

    runner = RateLimitedResumePi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        mock_af3_seconds=0.1, resume_retry_seconds=0.02,
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler required.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "运行 AF3"})).json()["run_id"]
            for _ in range(100):
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                                   headers=headers)).text)
                if run_events and run_events[-1]["type"] == "run.failed":
                    break
                await asyncio.sleep(0.01)
            entries = (await client.get("/api/v1/usage/entries", headers=headers)).json()
    assert runner.attempts == 1
    assert run_events[-1]["data"]["code"] == "MODEL_GATEWAY_RATE_LIMITED"
    assert "upstream secret" not in json.dumps(run_events)
    token_amounts = [entry["amount"] for entry in entries if entry["resource"] == "tokens"]
    assert len(token_amounts) == 3 and sum(token_amounts) == token_amounts[0]


@pytest.mark.asyncio
async def test_af3_wakeup_respects_users_remaining_token_quota(tmp_path):
    runner = ToolCallingFakePi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-key", admin_api_key="admin-key",
    ), pi_runner=runner)
    runner.app = app
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler required.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client)
            user_id = (await client.get("/api/v1/me", headers=headers)).json()["id"]
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "运行 AF3"})).json()["run_id"]
            for _ in range(100):
                if runner.job_id:
                    break
                await asyncio.sleep(0.01)
            limited = await client.put(f"/api/v1/admin/users/{user_id}/limits",
                                       headers={"X-Admin-Key": "admin-key"},
                                       json={"token_monthly_limit": 0, "gpu_daily_minutes": 60})
            finished = await client.post(f"/internal/af3/jobs/{runner.job_id}/result",
                                         headers={"X-Compute-Key": "compute-key"},
                                         json={"status": "completed", "actual_gpu_minutes": 12})
            for _ in range(100):
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                                   headers=headers)).text)
                if run_events and run_events[-1]["type"] == "run.failed":
                    break
                await asyncio.sleep(0.01)
    assert limited.status_code == finished.status_code == 200
    assert not any(prompt.startswith("/pskit_resume ") for prompt in runner.prompts)
    assert run_events[-1]["data"]["code"] == "TOKEN_QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_safe_interrupted_pi_run_retries_after_restart(tmp_path):
    class BlockingPi:
        def __init__(self):
            self.started = asyncio.Event()

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.started.set()
            await asyncio.Event().wait()

    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                        resume_retry_seconds=0.02)
    runner = BlockingPi()
    app = create_app(settings, pi_runner=runner)

    async def first_process():
        async with app.router.lifespan_context(app):  # noqa: SIM117 - Start lifespan first.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                headers = await login(client)
                project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
                session_id = f"session-{project_id.removeprefix('project-')}"
                run_id = (
                    await client.post(f"/api/v1/c/{session_id}/messages", headers=headers, json={"content": "你好"})
                ).json()["run_id"]
                await asyncio.wait_for(runner.started.wait(), timeout=0.5)
                return run_id

    run_id = await first_process()
    restarted = create_app(settings, pi_runner=FakePi())
    async with restarted.router.lifespan_context(restarted):  # noqa: SIM117 - Start lifespan first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test"
        ) as client:
            headers = await login(client)
            for _ in range(100):
                status = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
                if status.json()["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            run_events = events((await client.get(
                f"/api/v1/runs/{run_id}/events?follow=false", headers=headers,
            )).text)
    assert status.json()["status"] == "completed"
    assert [event["type"] for event in run_events if event["type"].startswith("run.")] == [
        "run.retrying", "run.completed",
    ]


@pytest.mark.asyncio
async def test_second_python_instance_does_not_recover_an_active_pi_run(tmp_path):
    class BlockingPi:
        def __init__(self):
            self.started = asyncio.Event()

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.started.set()
            await asyncio.Event().wait()

    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "shared.sqlite3"))
    first_runner = BlockingPi()
    first = create_app(settings, pi_runner=first_runner)
    second_runner = FakePi()
    second = create_app(settings, pi_runner=second_runner)
    async with first.router.lifespan_context(first):  # noqa: SIM117 - Both instances share SQLite.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
            headers = await login(client)
            project_id = (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            session_id = f"session-{project_id.removeprefix('project-')}"
            run_id = (await client.post(f"/api/v1/c/{session_id}/messages",
                                        headers=headers, json={"content": "研究问题"})).json()["run_id"]
            await first_runner.started.wait()
            async with second.router.lifespan_context(second):
                await asyncio.sleep(0.1)
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["status"]
                run_events = events((await client.get(f"/api/v1/runs/{run_id}/events?follow=false",
                                                   headers=headers)).text)

    assert status == "running"
    assert not any(event["type"] == "run.failed" for event in run_events)
    assert second_runner.prompts == []
