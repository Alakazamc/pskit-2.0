"""Publication grants protect send and actual upstream calls, including stale catalogs."""

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.domain.admin.model_policy import ModelPolicy
from app.main import create_app
from app.services.model_catalog import ModelCatalog


async def setup(client, app):
    login = (await client.post("/api/v1/auth/demo", json={"email": "operator@example.org"})).json()
    uid = login["user"]["id"]
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    app.state.admin_store.grant(
        uid, "platform_admin", actor="server:test", reason="Controlled model test"
    )
    return uid, headers


def make_app(tmp_path):
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=str(tmp_path / "models.db"),
            model_gateway_base_url="http://gateway/v1",
            model_gateway_model="lab-chat",
            model_gateway_api_key="server-only",
            model_policy_mode="managed",
        ),
        pi_runner=object(),
    )

    def discovery(request):
        return httpx.Response(200, json={"data": [{"id": "lab-chat"}]})

    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway",
        api_key="server-only",
        default_model="lab-chat",
        transport=httpx.MockTransport(discovery),
    )
    app.state.model_policy = ModelPolicy(
        app.state.admin_store, app.state.model_catalog, managed=True
    )
    return app


@pytest.mark.asyncio
async def test_hidden_alias_cannot_be_sent_by_api(tmp_path):
    app = make_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        _uid, h = await setup(client, app)
        project = (await client.get("/api/v1/g", headers=h)).json()[0]
        session = project["id"].replace("project-", "session-")
        assert (await client.get("/api/v1/models", headers=h)).json() == []
        response = await client.post(
            f"/api/v1/c/{session}/messages",
            headers=h,
            json={"content": "forged alias request", "model": "lab-chat"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "MODEL_FORBIDDEN"
        assert (await client.get(f"/api/v1/c/{session}/messages", headers=h)).json() == []


@pytest.mark.asyncio
async def test_policy_revocation_applies_to_next_model_call(tmp_path):
    app = make_app(tmp_path)
    forwarded = []

    def upstream(request):
        forwarded.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            uid, h = await setup(client, app)
            await client.put(
                "/api/v1/admin/llm-aliases/lab-chat/draft",
                headers=h,
                json={
                    "expected_revision": 0,
                    "reason": "Publish reviewed model",
                    "allowed_user_ids": [uid],
                    "purposes": ["chat"],
                },
            )
            await client.post(
                "/api/v1/admin/llm-aliases/lab-chat/publish",
                headers=h,
                json={"expected_revision": 1, "reason": "Approve model execution"},
            )
            store = app.state.conversations
            project = store.project_for(uid)
            session = store.create_session(uid, project.id, "Model policy")
            run = store.accept_message(
                uid,
                session.id,
                MessageRequest(content="analyze"),
                None,
                1,
                "run-model",
                "fingerprint",
                model_id="lab-chat",
            )[0]
            assert store.claim_initial_run(run.run_id)
            auth = {
                "Authorization": f"Bearer {run.run_id}.{app.state.agent_service.tool_token(run.run_id)}"
            }
            body = {
                "model": "lab-chat",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 5,
            }
            assert (
                await client.post("/internal/model/v1/chat/completions", headers=auth, json=body)
            ).status_code == 200
            before = store.usage_for(uid)
            await client.post(
                "/api/v1/admin/llm-aliases/lab-chat/retire",
                headers=h,
                json={"expected_revision": 2, "reason": "Revoke current grant"},
            )
            denied = await client.post(
                "/internal/model/v1/chat/completions", headers=auth, json=body
            )
            assert denied.status_code == 403
            assert len(forwarded) == 1
            assert store.usage_for(uid) == before


@pytest.mark.asyncio
async def test_gateway_fallback_does_not_bypass_publication(tmp_path):
    app = make_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        uid, h = await setup(client, app)
        await client.put(
            "/api/v1/admin/llm-aliases/lab-chat/draft",
            headers=h,
            json={
                "expected_revision": 0,
                "reason": "Reviewed model policy",
                "allowed_user_ids": [uid],
            },
        )
        await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/publish",
            headers=h,
            json={"expected_revision": 1, "reason": "Publish model policy"},
        )
        await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/retire",
            headers=h,
            json={"expected_revision": 2, "reason": "Retire model policy"},
        )
        app.state.model_catalog = ModelCatalog(
            base_url="http://gateway",
            api_key="server-only",
            default_model="lab-chat",
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )
        app.state.model_policy = ModelPolicy(
            app.state.admin_store, app.state.model_catalog, managed=True
        )
        assert (await client.get("/api/v1/models", headers=h)).json() == []
        with pytest.raises(PermissionError):
            await app.state.model_policy.authorize(uid, "lab-chat", "chat")


@pytest.mark.asyncio
async def test_analysis_purpose_requires_its_own_publication_grant(tmp_path):
    app = make_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        uid, h = await setup(client, app)
        await client.put(
            "/api/v1/admin/llm-aliases/lab-chat/draft",
            headers=h,
            json={
                "expected_revision": 0,
                "reason": "Publish chat purpose only",
                "allowed_user_ids": [uid],
                "purposes": ["chat"],
            },
        )
        await client.post(
            "/api/v1/admin/llm-aliases/lab-chat/publish",
            headers=h,
            json={"expected_revision": 1, "reason": "Approve chat purpose only"},
        )
        assert [m.id for m in await app.state.model_policy.visible_for(uid, "chat")] == ["lab-chat"]
        with pytest.raises(PermissionError):
            await app.state.model_policy.authorize(uid, "lab-chat", "analysis")


@pytest.mark.asyncio
async def test_managed_proxy_does_not_widen_published_image_capability(tmp_path):
    app = make_app(tmp_path)
    forwarded = []

    def upstream(request):
        forwarded.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            uid, h = await setup(client, app)
            await client.put(
                "/api/v1/admin/llm-aliases/lab-chat/draft",
                headers=h,
                json={
                    "expected_revision": 0,
                    "reason": "Publish text purpose only",
                    "allowed_user_ids": [uid],
                    "supports_images": False,
                },
            )
            await client.post(
                "/api/v1/admin/llm-aliases/lab-chat/publish",
                headers=h,
                json={"expected_revision": 1, "reason": "Approve text model policy"},
            )
            store = app.state.conversations
            project = store.project_for(uid)
            session = store.create_session(uid, project.id, "Text model policy")
            run = store.accept_message(
                uid,
                session.id,
                MessageRequest(content="analyze"),
                None,
                1,
                "text-run",
                "fingerprint",
                model_id="lab-chat",
            )[0]
            assert store.claim_initial_run(run.run_id)
            before = store.usage_for(uid)
            auth = {
                "Authorization": f"Bearer {run.run_id}.{app.state.agent_service.tool_token(run.run_id)}"
            }
            denied = await client.post(
                "/internal/model/v1/chat/completions",
                headers=auth,
                json={
                    "model": "lab-chat",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                                }
                            ],
                        }
                    ],
                    "max_tokens": 5,
                },
            )
            assert denied.status_code == 422
            assert forwarded == []
            assert store.usage_for(uid) == before


@pytest.mark.asyncio
async def test_model_proxy_accepts_ten_images_and_rejects_eleven(tmp_path):
    app = make_app(tmp_path)
    received = []

    def discovered(request):
        return httpx.Response(
            200, json={"data": [{"id": "lab-chat", "model_info": {"supports_vision": True}}]}
        )

    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway",
        api_key="server-only",
        default_model="lab-chat",
        transport=httpx.MockTransport(discovered),
    )
    app.state.model_policy = ModelPolicy(
        app.state.admin_store, app.state.model_catalog, managed=True
    )

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as gateway:
        app.state.model_gateway_http_client = gateway
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            uid, h = await setup(client, app)
            draft = await client.put(
                "/api/v1/admin/llm-aliases/lab-chat/draft",
                headers=h,
                json={
                    "expected_revision": 0,
                    "reason": "Publish approved image model",
                    "allowed_user_ids": [uid],
                    "supports_images": True,
                },
            )
            assert draft.status_code == 200
            assert (
                await client.post(
                    "/api/v1/admin/llm-aliases/lab-chat/publish",
                    headers=h,
                    json={"expected_revision": 1, "reason": "Approve image model policy"},
                )
            ).status_code == 200
            store = app.state.conversations
            project = store.project_for(uid)
            session = store.create_session(uid, project.id, "Image model policy")
            run = store.accept_message(
                uid,
                session.id,
                MessageRequest(content="analyze"),
                None,
                1,
                "ten-images",
                "fingerprint",
                model_id="lab-chat",
            )[0]
            assert store.claim_initial_run(run.run_id)
            auth = {
                "Authorization": f"Bearer {run.run_id}.{app.state.agent_service.tool_token(run.run_id)}"
            }
            # Twenty MB of base64 transport exceeds the former sixteen MB cap,
            # while each image is below the four MB source-image allowance.
            content = [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + "A" * 2_000_000},
                }
                for _ in range(10)
            ]
            body = {
                "model": "lab-chat",
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 5,
            }
            response = await client.post(
                "/internal/model/v1/chat/completions", headers=auth, json=body
            )
            assert response.status_code == 200
            assert len(received) == 1
            content.append(content[0])
            assert (
                await client.post("/internal/model/v1/chat/completions", headers=auth, json=body)
            ).status_code == 413
            assert len(received) == 1
