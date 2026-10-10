"""Qualification is opt-in and cannot bypass the normal Pi authentication gate."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.internal import proxy_model_call
from app.config import Settings
from app.main import create_app


def test_default_factory_has_no_qualification_driver():
    app = create_app(Settings())
    assert app.state.model_qualifier is None


@pytest.mark.parametrize("changes", [{}, {"mode": "live"}, {
    "mode": "live", "model_policy_mode": "managed", "agent_runtime": "pi",
    "pi_execution": "sandbox", "model_qualification_environment": "offline",
}])
def test_qualification_cannot_enable_outside_explicit_staging(changes):
    with pytest.raises(ValueError, match="MODEL_QUALIFICATION_CONFIG_INVALID"):
        create_app(Settings(model_qualification_enabled=True, **changes))


@pytest.mark.asyncio
async def test_qualification_delegate_follows_auth_model_and_route_checks():
    calls = []

    class Qualifier:
        async def proxy(self, request, *, authorization):
            calls.append(authorization)
            return JSONResponse({"qualification": "bounded"})

    store = SimpleNamespace(owner_for_run=lambda _: "audit-user",
                            run_context=lambda _: {"model_id": "alias"})
    service = SimpleNamespace(model_gateway_api_key="synthetic-private-key",
                              verify_tool_token=lambda run, token: token == "ok")
    state = SimpleNamespace(settings=Settings(model_gateway_base_url="https://gateway.invalid"),
                            agent_service=service, conversations=store, model_qualifier=Qualifier())

    async def request_for(body):
        raw = json.dumps(body).encode()
        async def receive():
            return {"type": "http.request", "body": raw}
        return Request({"type": "http", "method": "POST", "path": "/internal/model/v1/chat/completions",
                        "headers": [], "app": SimpleNamespace(state=state)}, receive)

    for auth, body, code in [("Bearer run.bad", {"model": "alias"}, 401),
                              ("Bearer run.ok", {"model": "other"}, 400),
                              ("Bearer run.ok", {"model": "alias", "api_base": "https://forged.invalid"}, 400)]:
        with pytest.raises(HTTPException) as error:
            await proxy_model_call(await request_for(body), authorization=auth)
        assert error.value.status_code == code
        assert calls == []
    result = await proxy_model_call(await request_for({"model": "alias"}), authorization="Bearer run.ok")
    assert result.body == b'{"qualification":"bounded"}'
    assert calls == ["Bearer run.ok"]
