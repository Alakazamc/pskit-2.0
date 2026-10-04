"""Validate drain, output collection and committed turns through the full HTTP application."""

import asyncio
import hashlib
import hmac
from pathlib import Path

import httpx
import pytest
from test_sandbox_lifecycle import AUTH, DockerDouble
from test_sandbox_lifecycle import settings as manager_settings

from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.config import Settings
from app.contracts.models import UserIdentity
from app.domain.sandboxes import SandboxActivityStore
from app.main import create_app
from app.sandbox_bridge import create_bridge_app
from app.sandbox_manager import create_manager_app
from app.services.model_catalog import ModelCatalog


@pytest.mark.asyncio
async def test_drained_turn_commits_its_transcript_and_owned_outputs(
    live_database, tmp_path, monkeypatch
):
    dsn, schema = live_database
    entered, finish = asyncio.Event(), asyncio.Event()
    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path))

    class Identity:
        async def verify(self, token):
            if token not in {"alice-test-only", "bob-test-only"}:
                return None
            owner = token.split("-")[0]
            return UserIdentity(id=owner, email=f"{owner}@example.invalid", name=owner)

    class Pi:
        async def prompt(self, session, message, on_event, **kwargs):
            output = Path(kwargs["environment"]["PSKIT_ARTIFACT_DIR"])
            (output / "result.txt").write_bytes(b"owned output")
            transcript = tmp_path / session / ".pi" / "turn.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text('{"type":"session","test_only":true}\n')
            entered.set()
            await finish.wait()
            return {"text": "Completed analysis", "session_file": str(transcript)}

    runner = SandboxPiRunner(
        manager_url="http://manager", manager_token="manager-secret-123456", model="lab"
    )
    app = create_app(
        Settings(
            mode="live",
            agent_runtime="pi",
            database_url=dsn,
            database_schema=schema,
            supabase_url="http://auth",
            supabase_publishable_key="test-only",
            model_gateway_base_url="http://gateway/v1",
            model_gateway_model="lab",
            model_gateway_api_key="test-only",
            pi_execution="sandbox",
            sandbox_manager_url="http://manager",
            sandbox_manager_token="manager-secret-123456",
        ),
        pi_runner=runner,
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
    token = hmac.new(b"derived-token-secret", b"alice", hashlib.sha256).hexdigest()
    manager = create_manager_app(
        manager_settings(),
        activity_store=SandboxActivityStore(app.state.database),
        docker_transport=httpx.MockTransport(DockerDouble()),
        bridge_transport=httpx.ASGITransport(app=create_bridge_app("alice", token, lambda _: Pi())),
    )
    runner.manager_transport = httpx.ASGITransport(app=manager)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://app",
            headers={"Authorization": "Bearer alice-test-only"},
        ) as client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager),
            base_url="http://manager",
            headers=AUTH,
        ) as control,
    ):
        session = (await client.post("/api/v1/c", json={"title": "Drain lifecycle"})).json()
        accepted = await client.post(
            f"/api/v1/c/{session['id']}/messages", json={"content": "Create an output"}
        )
        assert accepted.status_code == 200, accepted.text
        run_id = accepted.json()["run_id"]
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            summary = (await control.get("/v1/sandboxes")).json()[0]
            drained = await control.post(
                "/v1/sandboxes/alice/drain", json={"expected_revision": summary["revision"]}
            )
            assert drained.status_code == 200, drained.text
        finally:
            finish.set()
        async with asyncio.timeout(10):
            while True:
                status = (await client.get(f"/api/v1/runs/{run_id}")).json()
                if status["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.02)
        assert status["status"] == "completed", status
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
