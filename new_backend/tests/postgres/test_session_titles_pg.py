"""The public naming flow uses production PostgreSQL persistence and quota accounting."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.contracts.models import UserIdentity
from app.db.postgres_migrations import migrate_postgres
from app.main import create_app


class Identity:
    async def verify(self, token):
        return UserIdentity(id="alice", name="Alice", email="alice@example.org")


class Pi:
    async def prompt(self, session_id, message, on_event, **kwargs):
        await on_event({"type": "message_end", "message": {
            "role": "assistant", "usage": {"totalTokens": 30},
        }})
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "A completed protein analysis."}


@pytest.mark.asyncio
async def test_postgres_names_a_completed_chat_once_and_preserves_it_across_restart(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    settings = Settings(
        mode="live", agent_runtime="pi", database_url=dsn, database_schema=schema,
        mcp_executor="disabled", af3_executor="disabled", anonymous_enabled=False,
        supabase_url="https://identity.invalid", supabase_publishable_key="test-only-key",
        model_gateway_base_url="https://gateway.invalid", model_gateway_api_key="test-only-key",
        model_gateway_model="chat-large", title_model="title-small",
    )
    requests = []

    async def gateway(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "chat-large"}, {"id": "title-small"}]})
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": []})
        assert request.url.path == "/v1/chat/completions"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Protein structure analysis"}}],
            "usage": {"total_tokens": 13},
        })

    headers = {"Authorization": "Bearer test-only"}
    path = None
    for _ in range(2):
        app = create_app(settings, pi_runner=Pi())
        app.state.identity_provider = Identity()
        app.state.model_catalog.transport = httpx.MockTransport(gateway)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            if path is None:
                created = await client.post("/api/v1/c", headers=headers, json={
                    "title": "Temporary title", "auto_title": True,
                })
                assert created.status_code == 201
                path = f"/api/v1/c/{created.json()['id']}"
                sent = await client.post(f"{path}/messages", headers=headers, json={
                    "content": "Analyze this protein", "model": "chat-large",
                })
                assert sent.status_code == 200
            for _ in range(200):
                session = (await client.get(path, headers=headers)).json()
                if session.get("title_status") == "generated":
                    break
                await asyncio.sleep(0.01)
            assert session["title_status"] == "generated", session
            assert session["title"] == "Protein structure analysis"
            assert session["status"] == "completed"
            assert len(requests) == 1 and requests[0]["model"] == "title-small"
            assert (await client.get("/api/v1/usage", headers=headers)).json()["tokens"]["used"] == 43
            activity = (await client.get("/api/v1/usage/activity?days=1", headers=headers)).json()
            assert activity["days"][0]["tokens"] == 43
