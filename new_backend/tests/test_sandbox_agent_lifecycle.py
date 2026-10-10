"""Validate drain, output collection and committed turns through the full HTTP application."""

import asyncio

import httpx
import pytest
from workspace_provider_support import FilesystemWorkspaceProvider, workspace_settings

from app.config import Settings
from app.contracts.models import UserIdentity
from app.main import create_app
from app.services.model_catalog import ModelCatalog


@pytest.mark.asyncio
async def test_active_turn_blocks_drain_then_preserves_its_transcript_and_owned_outputs(
    live_database, tmp_path, monkeypatch
):
    dsn, schema = live_database
    entered, finish = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        "app.main.OpenSandboxWorkspaceProvider",
        lambda _settings, store: FilesystemWorkspaceProvider(tmp_path, store=store),
    )

    class Identity:
        async def verify(self, token):
            if token not in {"alice-test-only", "bob-test-only"}:
                return None
            owner = token.split("-")[0]
            return UserIdentity(id=owner, email=f"{owner}@example.invalid", name=owner)

    class Pi:
        async def prompt(self, session, message, on_event, **kwargs):
            attempt = kwargs["environment"]["PSKIT_ATTEMPT_ID"]
            output = tmp_path / session / "artifacts" / attempt
            output.mkdir(parents=True, exist_ok=True)
            (output / "result.txt").write_bytes(b"owned output")
            transcript = tmp_path / session / ".pi" / "turn.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text('{"type":"session","test_only":true}\n')
            entered.set()
            await finish.wait()
            return {"text": "Completed analysis", "session_file": str(transcript)}

    app = create_app(
        Settings(
            auth_csrf_secret="test-only-csrf-secret-at-least-32-bytes",
            mode="live",
            agent_runtime="pi",
            database_url=dsn,
            database_schema=schema,
            supabase_url="http://auth",
            supabase_publishable_key="test-only",
            model_gateway_base_url="http://gateway/v1",
            model_gateway_model="lab",
            model_gateway_api_key="test-only",
            **workspace_settings(),
        ),
        pi_runner=Pi(),
    )
    app.state.identity_provider = Identity()
    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway",
        api_key="test-only",
        default_model="lab",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"id": "lab"}]})
        ),
    )
    app.state.admin_store.grant(
        "alice", "platform_admin", actor="test-bootstrap", reason="Workspace lifecycle regression",
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://app",
            headers={"Authorization": "Bearer alice-test-only"},
        ) as client,
    ):
        session = (await client.post("/api/v1/c", json={"title": "Drain lifecycle"})).json()
        accepted = await client.post(
            f"/api/v1/c/{session['id']}/messages", json={"content": "Create an output"}
        )
        assert accepted.status_code == 200, accepted.text
        run_id = accepted.json()["run_id"]
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            summary = (await client.get("/api/v1/admin/sandboxes")).json()["items"][0]
            assert summary["active_sessions"] == [session["id"]]
            denied_drain = await client.post(
                "/api/v1/admin/sandboxes/alice/drain",
                json={"expected_revision": summary["revision"], "reason": "Lifecycle regression"},
            )
            assert denied_drain.status_code == 409, denied_drain.text
            assert denied_drain.json()["detail"]["code"] == "REVISION_CONFLICT"
        finally:
            finish.set()
        async with asyncio.timeout(10):
            while True:
                status = (await client.get(f"/api/v1/runs/{run_id}")).json()
                if status["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.02)
        assert status["status"] == "completed", status
        summary = (await client.get("/api/v1/admin/sandboxes")).json()["items"][0]
        assert summary["active_sessions"] == []
        drained = await client.post(
            "/api/v1/admin/sandboxes/alice/drain",
            json={"expected_revision": summary["revision"], "reason": "Lifecycle regression"},
        )
        assert drained.status_code == 200, drained.text
        stopped = (await client.get("/api/v1/admin/sandboxes")).json()["items"][0]
        assert stopped["runtime_state"] == "stopped"
        assert stopped["volume_id"] == summary["volume_id"]
        messages = (await client.get(f"/api/v1/c/{session['id']}/messages")).json()
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["parts"][0]["text"] == "Completed analysis"
        assert app.state.conversations.session_file_for("alice", session["id"]) == str(
            tmp_path / session["id"] / ".pi" / "turn.jsonl"
        )
        artifacts = (await client.get("/api/v1/artifacts")).json()
        output = next(item for item in artifacts if item["name"] == "result.txt")
        assert (
            await client.get(f"/api/v1/artifacts/{output['id']}/download")
        ).content == b"owned output"
        denied = await client.get(
            f"/api/v1/artifacts/{output['id']}/download",
            headers={"Authorization": "Bearer bob-test-only"},
        )
        assert denied.status_code == 404
