"""Trusted wire policy must match ordinary Pi requests without losing quota holds."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy
from app.services.model_gateway import prepare_model_parameters
from tests.test_model_proxy_transport import WaitingPi, active_run, application


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("override", [
    {"drop_params": True}, {"drop_params": False}, {"drop_params": None},
    {"allowed_openai_params": []}, {"allowed_openai_params": ["reasoning_effort"]},
    {"additional_drop_params": ["tools"]}, {"additional_drop_params": None},
    {"extra_body": {"max_tokens": 1_000_000}},
    {"extra_body": {"tools": []}},
    {"extra_body": {"tool_choice": "none"}},
    {"extra_body": {"reasoning_effort": "none"}},
])
async def test_client_parameter_policy_overrides_are_rejected_before_quota_or_dispatch(
    tmp_path, monkeypatch, override, preserve,
):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    install_profile(app, preserve=preserve)
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            quota_calls = []
            reserve = app.state.conversations.reserve_model_call

            def track_reservation(*args, **kwargs):
                quota_calls.append((args, kwargs))
                return reserve(*args, **kwargs)

            monkeypatch.setattr(app.state.conversations, "reserve_model_call", track_reservation)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100, **override,
            })
            assert response.status_code == 400
            assert response.json() == {"detail": {"code": "MODEL_REQUEST_OVERRIDE_FORBIDDEN"}}
            assert sent == []
            assert quota_calls == []
            assert app.state.conversations._unreported_model_reserve(owner, run_id) == 0
        assert not gateway.is_closed


@pytest.mark.parametrize("preserve", [None, False])
def test_default_model_policy_does_not_change_payload_or_input_objects(preserve):
    profile = None if preserve is None else ModelRuntimeProfile(alias="legacy", revision=1)
    payload = {"model": "legacy", "messages": [{"role": "user", "content": "qualification"}],
        "tools": [{"type": "function", "function": {"name": "qualification_echo"}}],
        "reasoning_effort": "high", "metadata": {"note": "preserve ordinary fields"}}
    original = json.loads(json.dumps(payload))
    result = prepare_model_parameters(payload, profile)
    assert result == original and payload == original
    assert result is not payload


@pytest.mark.parametrize("features,expected", [
    ({}, []),
    ({"tools": []}, ["tools"]),
    ({"tool_choice": "auto"}, ["tool_choice"]),
    ({"reasoning_effort": "none"}, ["reasoning_effort"]),
    ({"tools": [], "tool_choice": "required"}, ["tools", "tool_choice"]),
    ({"tools": [], "reasoning_effort": "high"}, ["tools", "reasoning_effort"]),
    ({"tool_choice": "auto", "reasoning_effort": "high"}, ["tool_choice", "reasoning_effort"]),
    ({"tools": [], "tool_choice": "auto", "reasoning_effort": "high"},
        ["tools", "tool_choice", "reasoning_effort"]),
])
def test_explicit_policy_preserves_only_parameters_in_the_actual_request(features, expected):
    profile = ModelRuntimeProfile(alias="compatible", revision=1,
        wire_policy=ModelWirePolicy(preserve_openai_parameters=True))
    payload = {"model": "compatible", "messages": [], "max_tokens": 8, **features}
    original = json.loads(json.dumps(payload))
    result = prepare_model_parameters(payload, profile)
    assert result == {**original, "drop_params": False,
        "allowed_openai_params": expected, "additional_drop_params": []}
    assert payload == original and result is not payload


def install_profile(app, *, preserve):
    profile = ModelRuntimeProfile(
        alias="lab-alias", revision=1, supports_tools=True, supports_tool_streaming=True,
        reasoning_levels=["off", "high"], max_output_tokens=8,
        wire_policy=ModelWirePolicy(
            reasoning_mode="reasoning_effort", preserve_openai_parameters=preserve,
        ),
    )
    model = SimpleNamespace(
        id="lab-alias", reasoning_levels=["off", "high"], supports_images=False,
        runtime_profile=profile,
    )

    class Policy:
        managed = True

        async def resolve(self, *_args):
            return model

        async def authorize(self, *_args):
            return model

    app.state.model_policy = Policy()
    return profile


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("status,upstream_body,error_code,held", [
    (200, {"choices": [{"message": {"content": "qualification"}}]}, None, True),
    (400, {"error": {"code": "unsupported_parameter", "message": "private supplier"}},
        "MODEL_GATEWAY_INVALID_REQUEST", False),
    (503, {"error": {"message": "private supplier"}}, "MODEL_GATEWAY_UNAVAILABLE", True),
])
async def test_trusted_profile_controls_real_proxy_wire_without_changing_quota_semantics(
    tmp_path, preserve, status, upstream_body, error_code, held,
):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    profile = install_profile(app, preserve=preserve)
    original_profile = profile.model_dump(mode="json")
    sent = []
    tools = [{"type": "function", "function": {"name": "qualification_echo",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}}}}]

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(status, json=upstream_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100,
                "tools": tools, "tool_choice": "auto", "reasoning_effort": "high",
            })
            assert response.status_code == status
            assert len(sent) == 1
            payload = sent[0]
            if preserve:
                assert payload["drop_params"] is False
                assert payload["allowed_openai_params"] == [
                    "tools", "tool_choice", "reasoning_effort",
                ]
                assert payload["additional_drop_params"] == []
            else:
                assert not {
                    "drop_params", "allowed_openai_params", "additional_drop_params",
                } & payload.keys()
            assert payload["max_tokens"] == 8
            assert payload["tools"] == tools
            assert payload["tool_choice"] == "auto" and payload["reasoning_effort"] == "high"
            assert (app.state.conversations._unreported_model_reserve(owner, run_id) > 0) is held
            if error_code:
                assert response.json() == {"error": {"code": error_code, "message": error_code}}
                assert "private supplier" not in response.text
            else:
                assert response.json() == upstream_body
            assert profile.model_dump(mode="json") == original_profile
        assert not gateway.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve", [False, True])
async def test_preserving_parameters_cannot_bypass_the_request_token_quota(tmp_path, preserve):
    runner = WaitingPi()
    app = application(tmp_path, runner)
    install_profile(app, preserve=preserve)
    sent = []

    def upstream(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owner, run_id, auth = await active_run(client, app, runner)
            app.state.conversations.set_token_limit(owner, 1)
            response = await client.post("/internal/model/v1/chat/completions", headers=auth, json={
                "model": "lab-alias", "messages": [], "max_tokens": 100,
                "reasoning_effort": "high",
            })
            assert response.status_code == 402
            assert response.json() == {"error": {
                "message": "PSKIT_TOKEN_QUOTA_EXCEEDED", "code": "PSKIT_TOKEN_QUOTA_EXCEEDED",
            }}
            assert sent == []
            assert app.state.conversations._unreported_model_reserve(owner, run_id) == 0
        assert not gateway.is_closed
