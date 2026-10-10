"""Title calls reuse the pool while retaining separate authorization and accounting."""

from types import SimpleNamespace

import httpx
import pytest

from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy
from app.services.session_titles import SessionTitleService


class TitleStore:
    def __init__(self):
        self.holds = []
        self.attempts = []
        self.settled = []
        self.titles = []

    def run_context(self, _run_id):
        return {"model_id": "chat-model"}

    def title_request_active(self, *_args):
        return True

    def reserve_resume_tokens(self, *args):
        self.holds.append(args)

    def settle_current_tokens(self, *args, **kwargs):
        self.settled.append((args, kwargs))

    def record_model_attempt(self, *args):
        self.attempts.append(args)

    def finish_session_title(self, *args):
        self.titles.append(args)


class TitlePolicy:
    def __init__(self, profile=None):
        self.calls = []
        self.profile = profile
        self.catalog = SimpleNamespace(base_url="https://gateway.invalid", api_key="private-key",
                                       transport=None)

    async def resolve(self, *args):
        self.calls.append(args)
        return SimpleNamespace(id="title-model", runtime_profile=self.profile)


@pytest.mark.asyncio
async def test_title_uses_injected_shared_pool_with_its_own_grant_hold_and_timeout():
    store, policy = TitleStore(), TitlePolicy()
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "New title"}}], "usage": {"total_tokens": 8},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as shared:
        service = SessionTitleService(store, policy, model="title-model", timeout=3,
                                      http_client=lambda: shared)
        await service._generate("session-1", "owner", "run-1", "synthetic input")
        assert not shared.is_closed
        assert policy.calls == [("owner", "title-model", "chat")]
        assert store.holds[0][:2] == ("owner", "run-1")
        assert store.attempts == [("owner", "run-1", 8, "completed")]
        assert store.settled == [(("owner", "run-1"), {})]
        assert ("owner", "session-1", "New title") in store.titles
        assert calls[0].extensions["timeout"]["read"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400, 404, 408, 422, 503])
async def test_missing_usage_and_ambiguous_failures_do_not_release_title_hold(status):
    store, policy = TitleStore(), TitlePolicy()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, json={"choices": [{"message": {"content": "New title"}}]},
    ))) as shared:
        await SessionTitleService(store, policy, http_client=lambda: shared)._generate(
            "session-1", "owner", "run-1", "synthetic input",
        )
    assert len(store.holds) == 1
    assert store.attempts == store.settled == []


@pytest.mark.asyncio
async def test_title_obeys_published_token_field_without_adding_reasoning_or_tools():
    import json

    store = TitleStore()
    policy = TitlePolicy(ModelRuntimeProfile(
        alias="title-model", revision=1, max_output_tokens=80,
        wire_policy=ModelWirePolicy(chat_max_tokens_field="max_completion_tokens"),
    ))
    bodies = []

    def upstream(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "New title"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as shared:
        await SessionTitleService(store, policy, http_client=lambda: shared)._generate(
            "session-1", "owner", "run-1", "synthetic input",
        )
    assert bodies[0]["max_completion_tokens"] == 80
    assert {"max_tokens", "reasoning_effort", "tools"}.isdisjoint(bodies[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(402, "insufficient_quota"), (400, "invalid_request_error")])
async def test_definite_structured_title_rejections_release_the_auxiliary_hold(status, code):
    store, policy = TitleStore(), TitlePolicy()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, json={"error": {"code": code, "message": "private supplier details"}},
    ))) as shared:
        await SessionTitleService(store, policy, http_client=lambda: shared)._generate(
            "session-1", "owner", "run-1", "synthetic input",
        )
    assert len(store.holds) == 1
    assert store.attempts == []
    assert store.settled == [(("owner", "run-1"), {"refund_if_unreported": True})]


@pytest.mark.asyncio
async def test_title_body_read_failure_closes_response_and_keeps_auxiliary_hold():
    store, policy = TitleStore(), TitlePolicy()

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
    ))) as shared:
        await SessionTitleService(store, policy, http_client=lambda: shared)._generate(
            "session-1", "owner", "run-1", "synthetic input",
        )
        assert body.closed and not shared.is_closed
    assert len(store.holds) == 1 and store.attempts == store.settled == []
