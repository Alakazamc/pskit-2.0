"""Shared model transport ownership and secret-safe upstream errors."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.services.model_gateway import (
    acquire_gateway_client,
    create_gateway_client,
    gateway_client_lifespan,
    normalize_gateway_error,
    read_gateway_error,
)


@pytest.mark.asyncio
async def test_lifespan_reuses_owned_client_and_closes_on_startup_failure():
    state = SimpleNamespace(model_catalog=SimpleNamespace(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True}),
    )))
    with pytest.raises(RuntimeError, match="startup"):
        async with gateway_client_lifespan(state):
            first = acquire_gateway_client(state)
            second = acquire_gateway_client(state)
            assert first.client is second.client is state.model_gateway_http_client
            assert first.owned is second.owned is False
            await first.aclose()
            assert not second.client.is_closed
            response = await second.client.get("https://example.invalid")
            assert response.json() == {"ok": True}
            raise RuntimeError("startup")
    assert first.client.is_closed
    assert not hasattr(state, "model_gateway_http_client")


@pytest.mark.asyncio
async def test_lifespan_never_closes_injected_client():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200),
    )) as injected:
        state = SimpleNamespace(model_gateway_http_client=injected)
        async with gateway_client_lifespan(state):
            lease = acquire_gateway_client(state)
            assert lease.client is injected and not lease.owned
        assert not injected.is_closed
        assert state.model_gateway_http_client is injected


@pytest.mark.asyncio
async def test_lifespan_closes_client_when_cancelled():
    state = SimpleNamespace()
    entered = asyncio.Event()

    async def run():
        async with gateway_client_lifespan(state):
            client = state.model_gateway_http_client
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                assert not client.is_closed

    task = asyncio.create_task(run())
    await entered.wait()
    client = state.model_gateway_http_client
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.is_closed


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {"error": {"message": "private token"}}, "MODEL_GATEWAY_AUTH_FAILED"),
    (403, {"error": "private URL"}, "MODEL_GATEWAY_AUTH_FAILED"),
    (429, {"error": {"code": "rate_limit_exceeded"}}, "MODEL_GATEWAY_RATE_LIMITED"),
    (429, {"error": {"type": "insufficient_quota"}}, "MODEL_GATEWAY_QUOTA_EXHAUSTED"),
    (400, {"error": {"code": "invalid_request_error"}}, "MODEL_GATEWAY_INVALID_REQUEST"),
    (402, {}, "MODEL_GATEWAY_QUOTA_EXHAUSTED"),
    (503, {}, "MODEL_GATEWAY_UNAVAILABLE"),
    (503, {"error": {"code": "insufficient_quota"}}, "MODEL_GATEWAY_UNAVAILABLE"),
    (418, {"error": {"code": "invalid_request_error"}}, "UNKNOWN_UPSTREAM_ERROR"),
    (400, {"error": {"message": "billing 401 429 secret"}}, "UNKNOWN_UPSTREAM_ERROR"),
    (400, {"error": {"code": "unrecognized", "message": "secret"}}, "UNKNOWN_UPSTREAM_ERROR"),
])
def test_structured_status_and_code_normalization_never_relays_private_body(status, body, code):
    error = normalize_gateway_error(httpx.Response(status, json=body))
    assert error.status_code == status
    assert error.code == code
    assert error.body() == {"error": {"code": code, "message": code}}
    assert error.definite_rejection is (code not in {"UNKNOWN_UPSTREAM_ERROR", "MODEL_GATEWAY_UNAVAILABLE"})
    assert "private" not in str(error.body()) and "secret" not in str(error.body())


def test_non_json_upstream_error_is_unknown_and_usage_is_not_fabricated():
    response = httpx.Response(418, text="private supplier traceback and credentials")
    error = normalize_gateway_error(response)
    assert error.code == "UNKNOWN_UPSTREAM_ERROR"
    assert "usage" not in error.body()


def test_retry_after_is_the_only_permitted_error_header():
    error = normalize_gateway_error(httpx.Response(429, json={}, headers={
        "retry-after": "3", "set-cookie": "supplier-secret", "x-debug-key": "private",
    }))
    assert error.headers == {"Retry-After": "3"}
    assert normalize_gateway_error(httpx.Response(429, json={}, headers={
        "retry-after": "secret-value",
    })).headers == {}


def test_pool_factory_has_explicit_proxy_limits_and_timeouts(monkeypatch):
    seen = []
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:9999")
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", lambda **kwargs: seen.append(kwargs))
    create_gateway_client()
    assert seen[0]["trust_env"] is False
    assert seen[0]["timeout"].connect == 10 and seen[0]["timeout"].pool == 10
    assert seen[0]["timeout"].read == 180
    assert seen[0]["limits"].max_connections == 100
    assert seen[0]["limits"].max_keepalive_connections == 20


@pytest.mark.asyncio
async def test_error_reader_is_bounded_and_does_not_buffer_arbitrary_supplier_body():
    class Body(httpx.AsyncByteStream):
        consumed = 0

        async def __aiter__(self):
            for _ in range(1000):
                self.consumed += 1
                yield b"x" * 1024

    body = Body()
    response = httpx.Response(400, stream=body)
    error = await read_gateway_error(response)
    assert error.code == "UNKNOWN_UPSTREAM_ERROR"
    assert body.consumed <= 65


@pytest.mark.asyncio
@pytest.mark.parametrize("status,mode", [(400, "broken"), (402, "broken"), (401, "oversized")])
async def test_unreadable_or_oversized_error_cannot_establish_definite_rejection(status, mode):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            if mode == "oversized":
                yield b"x" * 65537
            else:
                yield b'{"error":{"code":"invalid_request_error"'
                raise httpx.ReadError("private supplier body read failed")

    error = await read_gateway_error(httpx.Response(status, stream=Body()))
    assert error.status_code == status
    assert error.code == "UNKNOWN_UPSTREAM_ERROR"
    assert error.definite_rejection is False
