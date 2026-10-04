"""Bounded provider acceptance keeps production admin credentials out of Staging."""

import json

import httpx
import pytest
from app.contracts.conversation import MessageRequest
from app.services.model_catalog import ModelOption

from deploy.agent.tests import smoke_composer_models as smoke
from deploy.agent.tests.smoke_staging import SmokeFailure


@pytest.fixture
def smoke_setup(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    prod = tmp_path / "prod.env"
    for path, content in {
        stage / "litellm.env": "LITELLM_MASTER_KEY=stage-master\n",
        stage / "seed.env": "STAGING_USER_EMAIL=stage@example.invalid\nSTAGING_USER_PASSWORD=fixture\n",
        prod: "LITELLM_MASTER_KEY=prod-master\n",
    }.items():
        path.write_text(content)
        path.chmod(0o600)
    models = ["model-a", "model-b"]
    calls = []
    terminal = ["run.completed"]
    aliases = []

    class Catalog:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "sk-release-fixture"

        async def list_models(self):
            return tuple(ModelOption(id=model, reasoning_levels=["medium", "high"])
                         for model in models)

    def respond(request):
        payload = json.loads(request.content) if request.content else None
        calls.append((request.url.host, request.url.port, request.url.path, payload))
        path = request.url.path
        if path == "/key/generate":
            assert payload["models"] == models
            assert payload["max_budget"] == 0.25 and payload["duration"] == "20m"
            return httpx.Response(200, json={"key": "sk-release-fixture"})
        if path == "/model/new":
            assert payload["litellm_params"]["api_key"] == "sk-release-fixture"
            assert "prod-master" not in request.content.decode()
            aliases.append(payload["model_name"])
            return httpx.Response(200, json={"model_info": {"id": f"route-{len(aliases)}"}})
        if path == "/api/v1/models":
            return httpx.Response(200, json=[{"id": alias, "reasoning_levels": ["medium", "high"]}
                                          for alias in aliases])
        if path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "fixture-token"})
        if path == "/api/v1/g":
            return httpx.Response(200, json=[{"id": "project-stage", "name": "Staging research"}])
        if path == "/api/v1/g/g-p-stage/c":
            return httpx.Response(201, json={"id": "session-stage"})
        if path.endswith("/messages"):
            assert MessageRequest.model_validate(payload).model == aliases[0]
            assert payload["reasoning_effort"] == "medium"
            return httpx.Response(200, json={"run_id": "run-stage"})
        if path.endswith("/events"):
            events = [{"type": "message.delta"}, {"type": terminal[0]}]
            return httpx.Response(200, content="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        if path == "/api/v1/runs/run-stage":
            if "malformed-cleanup" in terminal:
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(200, json={"status": terminal[0].removeprefix("run.")})
        if path == "/key/info":
            return httpx.Response(200, json={"info": {"spend": 0.001}})
        if path in {"/key/delete", "/model/delete"}:
            return httpx.Response(200, json={})
        raise AssertionError(path)

    client = httpx.Client
    monkeypatch.setattr(smoke, "ModelCatalog", Catalog)
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(respond)))
    return stage, prod, models, calls, terminal


def test_live_smoke_limits_key_and_revokes_every_temporary_route(smoke_setup):
    stage, prod, models, calls, _ = smoke_setup
    result = smoke.run_live_smoke(stage, prod, models)
    assert result["terminal"] == "run.completed" and result["key_spend_usd"] == 0.001
    assert [path for _, _, path, _ in calls].count("/model/delete") == 2
    assert calls[-1][2:] == ("/key/delete", {"keys": ["sk-release-fixture"]})


def test_live_smoke_revokes_resources_after_provider_failure(smoke_setup):
    stage, prod, models, calls, terminal = smoke_setup
    terminal[0] = "run.failed"
    with pytest.raises(SmokeFailure, match="run.failed"):
        smoke.run_live_smoke(stage, prod, models)
    assert [path for _, _, path, _ in calls].count("/model/delete") == 2
    assert calls[-1][2] == "/key/delete"


def test_live_smoke_rejects_shared_master_before_network(smoke_setup):
    stage, prod, models, calls, _ = smoke_setup
    prod.write_text("LITELLM_MASTER_KEY=stage-master\n")
    with pytest.raises(ValueError, match="differ"):
        smoke.run_live_smoke(stage, prod, models)
    assert not calls


def test_live_smoke_rejects_real_account_before_network(smoke_setup):
    stage, prod, models, calls, _ = smoke_setup
    (stage / "seed.env").write_text("STAGING_USER_EMAIL=real@example.com\nSTAGING_USER_PASSWORD=fixture\n")
    with pytest.raises(ValueError, match="synthetic"):
        smoke.run_live_smoke(stage, prod, models)
    assert not calls


def test_live_smoke_revokes_key_when_run_cleanup_response_is_malformed(smoke_setup):
    stage, prod, models, calls, terminal = smoke_setup
    terminal.append("malformed-cleanup")
    with pytest.raises(SmokeFailure, match="cleanup"):
        smoke.run_live_smoke(stage, prod, models)
    assert [path for _, _, path, _ in calls].count("/model/delete") == 2
    assert calls[-1][2] == "/key/delete"
