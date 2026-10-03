"""LiteLLM model aliases and vision capability come from the gateway, not the browser."""

import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.services.model_catalog import ModelCatalog


@pytest.mark.asyncio
async def test_catalog_lists_accessible_aliases_and_only_confirmed_vision_models():
    requests = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer server-secret"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "text-model"}, {"id": "vision-model"}, {"id": "vision-model"},
            ]})
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": [
                {"model_name": "text-model", "model_info": {"supports_vision": False}},
                {"model_name": "vision-model", "model_info": {"supports_vision": True}},
            ]})
        raise AssertionError(str(request.url))

    catalog = ModelCatalog(
        base_url="http://gateway:4000/v1", api_key="server-secret",
        default_model="text-model", transport=httpx.MockTransport(gateway),
    )
    models = await catalog.list_models()

    assert [(item.id, item.supports_images) for item in models] == [
        ("text-model", False), ("vision-model", True),
    ]
    assert len(requests) == 2
    assert await catalog.resolve("vision-model") == models[1]
    with pytest.raises(ValueError, match="unavailable"):
        await catalog.resolve("hidden-model")


@pytest.mark.asyncio
async def test_catalog_fails_closed_when_vision_metadata_is_unavailable():
    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "custom-vision"}]})
        return httpx.Response(403)

    catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="custom-vision", transport=httpx.MockTransport(gateway),
    )
    models = await catalog.list_models()
    assert [(item.id, item.supports_images) for item in models] == [("custom-vision", False)]


@pytest.mark.asyncio
async def test_operator_can_mark_a_custom_alias_as_vision_capable():
    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "my-model"}]})
        return httpx.Response(404)

    catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="my-model", image_model_ids={"my-model"},
        transport=httpx.MockTransport(gateway),
    )
    assert (await catalog.resolve(None)).supports_images is True


@pytest.mark.asyncio
async def test_authenticated_model_list_only_exposes_public_alias_and_image_flag():
    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "visible-model"}]})
        return httpx.Response(200, json={"data": [{"model_name": "visible-model",
                                                 "model_info": {"supports_vision": True,
                                                                "api_key": "provider-secret"}}]})

    app = create_app(Settings())
    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="visible-model", transport=httpx.MockTransport(gateway),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://backend") as client:
        unauthorized = await client.get("/api/v1/models")
        login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
        response = await client.get("/api/v1/models", headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
        })
    assert unauthorized.status_code == 401
    assert response.json() == [{"id": "visible-model", "supports_images": True}]
    assert "provider-secret" not in response.text


@pytest.mark.asyncio
async def test_selected_model_is_validated_and_pinned_to_the_accepted_run(tmp_path):
    class RecordingPi:
        environment = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.environment = kwargs["environment"]
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "完成"}

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "default-model"}, {"id": "chosen-model"},
            ]})
        return httpx.Response(403)

    runner = RecordingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              model_gateway_model="default-model"), pi_runner=runner)
    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="default-model", transport=httpx.MockTransport(gateway),
    )
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Keep lifespan separate.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://backend") as client:
            login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            project = (await client.get("/api/v1/g", headers=headers)).json()[0]
            session_id = project["id"].replace("project-", "session-")
            path = f"/api/v1/c/{session_id}/messages"
            denied = await client.post(path, headers=headers, json={
                "content": "hello", "model": "not-visible",
            })
            accepted = await client.post(path, headers=headers, json={
                "content": "hello", "model": "chosen-model",
            })
            run_id = accepted.json()["run_id"]
            for _ in range(100):
                if runner.environment is not None:
                    break
                await asyncio.sleep(0.01)
    assert denied.status_code == 422
    assert accepted.status_code == 200
    assert app.state.conversations.run_context(run_id)["model_id"] == "chosen-model"
    assert runner.environment["PSKIT_MODEL_ID"] == "chosen-model"


@pytest.mark.asyncio
async def test_model_proxy_accepts_only_the_model_pinned_to_that_run(tmp_path):
    class BlockingPi:
        started = asyncio.Event()

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.started.set()
            await asyncio.Event().wait()

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "default-model"}, {"id": "chosen-model"},
            ]})
        if request.url.path == "/model/info":
            return httpx.Response(403)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        model_gateway_base_url="http://gateway:4000/v1", model_gateway_model="default-model",
    ), pi_runner=BlockingPi())
    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="default-model", transport=httpx.MockTransport(gateway),
    )
    app.state.agent_service.model_gateway_api_key = "server-secret"
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as upstream:
        app.state.model_gateway_http_client = upstream
        async with app.router.lifespan_context(app):  # noqa: SIM117 - Keep lifespan separate.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://backend") as client:
                login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                project = (await client.get("/api/v1/g", headers=headers)).json()[0]
                session_id = project["id"].replace("project-", "session-")
                accepted = await client.post(f"/api/v1/c/{session_id}/messages", headers=headers,
                                             json={"content": "hello", "model": "chosen-model"})
                run_id = accepted.json()["run_id"]
                await app.state.agent_service.runner.started.wait()
                auth = {"Authorization": f"Bearer {run_id}.{app.state.agent_service.tool_token(run_id)}"}
                wrong = await client.post("/internal/model/v1/chat/completions", headers=auth,
                                          json={"model": "default-model", "messages": [],
                                                "max_tokens": 5})
                chosen = await client.post("/internal/model/v1/chat/completions", headers=auth,
                                           json={"model": "chosen-model", "messages": [{
                                               "role": "user", "content": [{
                                                   "type": "image_url", "image_url": {
                                                       "url": "data:image/png;base64," + "A" * 1_500_000,
                                                   },
                                               }],
                                           }], "max_tokens": 5})
    assert wrong.status_code == 400
    assert chosen.status_code == 200
