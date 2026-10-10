"""The trusted Pi proxy keeps stream ownership and conservative usage holds."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from app.api.internal import proxy_model_call
from app.config import Settings
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy
from app.main import create_app


class WaitingPi:
    def __init__(self):
        self.started = asyncio.Event()

    async def prompt(self, *_args, **_kwargs):
        self.started.set()
        await asyncio.Event().wait()


async def active_run(client, app, runner):
    login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    session = (await client.post("/api/v1/c", headers=auth, json={"title": "Transport"})).json()
    run = (await client.post(f"/api/v1/c/{session['id']}/messages", headers=auth,
                             json={"content": "Hi"})).json()
    await runner.started.wait()
    run_id = run["run_id"]
    owner = app.state.conversations.owner_for_run(run_id)
    proxy_auth = {"Authorization": f"Bearer {run_id}.{app.state.agent_service.tool_token(run_id)}"}
    return owner, run_id, proxy_auth


def application(tmp_path, runner):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        model_gateway_base_url="https://gateway.invalid", model_gateway_model="lab-alias",
    ), pi_runner=runner)
    app.state.agent_service.model_gateway_api_key = "private-test-key"
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_code,reserved", [
    (429, "MODEL_GATEWAY_RATE_LIMITED", False),
    (503, "MODEL_GATEWAY_UNAVAILABLE", True),
    (418, "UNKNOWN_UPSTREAM_ERROR", True),
    (400, "UNKNOWN_UPSTREAM_ERROR", True),
    (404, "UNKNOWN_UPSTREAM_ERROR", True),
    (422, "UNKNOWN_UPSTREAM_ERROR", True),
    (402, "MODEL_GATEWAY_QUOTA_EXHAUSTED", False),
])
async def test_proxy_sanitizes_errors_but_keeps_ambiguous_holds(
    tmp_path, status, expected_code, reserved,
):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, json={"error": {"message": "private-test-key secret supplier URL"}},
        headers={"retry-after": "2", "x-private-secret": "private-test-key"},
    ))) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 100, "stream": True,
            })
            assert response.status_code == status
            assert response.json() == {"error": {"message": expected_code, "code": expected_code}}
            assert response.headers["retry-after"] == "2"
            assert "x-private-secret" not in response.headers
            assert "private-test-key" not in response.text and "usage" not in response.json()
            # Token holds are charged into `used`; the guard is the durable unknown-usage ledger.
            assert (app.state.conversations._unreported_model_reserve(owner, run_id) > 0) is reserved
        assert not injected.is_closed


@pytest.mark.asyncio
async def test_proxy_transport_timeout_keeps_hold_and_never_relays_private_exception(tmp_path):
    runner = WaitingPi()
    app = application(tmp_path, runner)

    def failed(request):
        raise httpx.ReadTimeout("private-test-key supplier host", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100,
            })
            assert response.status_code == 502
            assert response.json() == {"detail": "Model gateway request failed"}
            assert app.state.conversations._unreported_model_reserve(owner, run_id) > 0
            assert not injected.is_closed


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_cancelling_proxy_iterator_closes_response_without_closing_shared_pool(tmp_path):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    stream = TrackedStream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, stream=stream, headers={"content-type": "text/event-stream"},
    ))) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            _, _, auth = await active_run(client, app, runner)

            async def receive():
                return {"type": "http.request", "body": json.dumps({
                    "model": "lab-alias", "messages": [], "stream": True, "max_tokens": 100,
                }).encode()}

            request = Request({"type": "http", "app": app}, receive)
            response = await proxy_model_call(request, authorization=auth["Authorization"])
            iterator = response.body_iterator
            assert b'"first"' in await anext(iterator)
            await iterator.aclose()
            assert stream.closed
            assert not injected.is_closed


@pytest.mark.asyncio
async def test_conflicting_output_fields_are_rejected_before_reservation_or_upstream(tmp_path):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    requests = []

    def unexpected(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            before = app.state.conversations.usage_for(owner).tokens.used
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "max_tokens": 10, "max_completion_tokens": 999999,
            })
            assert response.status_code == 400
            assert requests == []
            assert app.state.conversations.usage_for(owner).tokens.used == before
            assert app.state.conversations._unreported_model_reserve(owner, run_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"api_base": "https://unapproved-provider.invalid/v1"},
    {"api_key": "caller-selected-private-key"},
])
async def test_run_scoped_token_cannot_override_server_owned_route_or_credential(tmp_path, override):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100, **override,
            })
            assert response.status_code == 400
            assert response.json() == {"detail": {"code": "MODEL_REQUEST_OVERRIDE_FORBIDDEN"}}
            assert requests == []
            assert app.state.conversations._unreported_model_reserve(owner, run_id) == 0


@pytest.mark.asyncio
async def test_normal_function_tools_with_api_key_business_parameter_are_forwarded_unchanged(tmp_path):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    bodies = []
    tools = [{"type": "function", "function": {
        "name": "synthetic_tool", "parameters": {"type": "object", "properties": {
            "api_key": {"type": "string"}, "base_url": {"type": "string"},
        }},
    }}]
    messages = [{"role": "assistant", "tool_calls": [{"id": "tool-1", "type": "function",
                 "function": {"name": "synthetic_tool", "arguments": '{"api_key":"test"}'}}]},
                {"role": "tool", "tool_call_id": "tool-1", "content": "synthetic result"}]

    def upstream(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            _, _, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": messages, "max_tokens": 100,
                "tools": tools, "tool_choice": "auto", "metadata": {"label": "synthetic"},
            })
            assert response.status_code == 200
    assert bodies[0]["tools"] == tools and bodies[0]["messages"] == messages
    assert bodies[0]["tool_choice"] == "auto" and bodies[0]["metadata"] == {"label": "synthetic"}


@pytest.mark.asyncio
@pytest.mark.parametrize("wire,current_levels,status", [
    ("none", ["off"], 200),
    ("off", ["off"], 200),
    ("none", ["high"], 422),
    ("disabled", ["off"], 422),
])
async def test_proxy_off_wire_mapping_requires_current_logical_grant_and_snapshot(
    tmp_path, wire, current_levels, status,
):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    profile = ModelRuntimeProfile(alias="lab-alias", revision=1, reasoning_levels=["off"],
                                  wire_policy=ModelWirePolicy(reasoning_mode="reasoning_effort"))
    initial = SimpleNamespace(id="lab-alias", reasoning_levels=["off"], supports_images=False,
                              runtime_profile=profile)
    current = SimpleNamespace(id="lab-alias", reasoning_levels=current_levels,
                              supports_images=False, runtime_profile=profile)

    class Policy:
        managed = True

        async def resolve(self, *_args):
            return initial

        async def authorize(self, *_args):
            return current

    app.state.model_policy = Policy()
    bodies = []

    def upstream(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100,
                "reasoning_effort": wire,
            })
            assert response.status_code == status
            if status == 200:
                assert bodies[0]["reasoning_effort"] == "none"
            else:
                assert response.json()["detail"]["code"] == "MODEL_REASONING_UNAVAILABLE"
                assert bodies == []
                assert app.state.conversations._unreported_model_reserve(owner, run_id) == 0


@pytest.mark.asyncio
async def test_proxy_body_read_failure_keeps_hold_even_if_headers_suggest_payment_rejection(tmp_path):
    runner = WaitingPi()
    app = application(tmp_path, runner)

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"error":'
            raise httpx.ReadError("private supplier failure")

        async def aclose(self):
            self.closed = True

    body = Body()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        402, stream=body,
    ))) as injected:
        app.state.model_gateway_http_client = injected
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100,
            })
            assert response.status_code == 402
            assert response.json()["error"]["code"] == "UNKNOWN_UPSTREAM_ERROR"
            assert app.state.conversations._unreported_model_reserve(owner, run_id) > 0
            assert body.closed and not injected.is_closed
