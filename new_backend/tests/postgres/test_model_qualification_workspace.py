"""Installed Pi writes/reads a synthetic artifact through existing owned records."""

import asyncio
import json
import os
import re
import socket
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Header, Request
from test_model_qualification_driver import Fragments, Provider
from workspace_provider_support import FilesystemWorkspaceProvider

from app.adapters.live.pi_rpc import PiRpcRunner
from app.api.internal_workspace import router as workspace_router
from app.contracts.admin import ModelDraft
from app.contracts.conversation import ArtifactCreatedEvent
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy
from app.db.postgres import PostgresDatabase, PostgresStatements
from app.db.postgres_migrations import migrate_postgres
from app.domain.admin.model_policy import ModelPolicy
from app.domain.admin.roles import AdminStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.sandboxes import SandboxArtifactStore
from app.domain.workspace_access import WorkspaceAccessPolicy
from app.domain.workspace_attempts import WorkspaceAttemptStore
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.services.agent import AgentService
from app.services.model_catalog import ModelCatalog
from app.services.model_qualification_driver import ModelQualificationDriver
from app.services.workspace_files import WorkspaceFiles
from app.services.workspace_transfer import WorkspaceTransfer


@pytest.fixture
def qualification_schema():
    """Keep the loopback qualification fixture independent of collection order."""
    import psycopg
    from psycopg import sql

    dsn = os.environ["TEST_POSTGRES_DSN"]
    schema = f"pskit_test_{uuid.uuid4().hex}"
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


class WorkspaceProvider(Provider):
    def __init__(self, *, fake_tools=False):
        super().__init__()
        self.deployments = ["deployment-a"]
        self.fake_tools = fake_tools
        self.pi_requests = []

    async def __call__(self, request):
        body = json.loads(request.content) if request.method == "POST" else {}
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if names != {"write_file", "read_file"}:
            return await super().__call__(request)
        self.pi_requests.append(body)
        assert body["model"] == "deployment-a"
        assert request.headers["authorization"] == "Bearer synthetic-gateway-key"
        def text(item):
            content = item.get("content")
            return content if isinstance(content, str) else "\n".join(
                block.get("text", "") for block in content or [] if block.get("type") == "text")
        content = "\n".join(text(item) for item in body["messages"] if item.get("role") in {"system", "user"})
        path = re.search(r"artifacts/workspace-[a-f0-9]+/qualification.txt", content).group()
        count = sum(item.get("role") == "tool" for item in body["messages"])
        if self.fake_tools or count >= 2:
            delta, finish = {"role": "assistant", "content": "qualification"}, "stop"
        else:
            name = "write_file" if count == 0 else "read_file"
            args = {"path": path, **({"content": "qualification"} if count == 0 else {})}
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"workspace-call-{count}",
                "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        chunks = [{"id": "qualification-pi", "object": "chat.completion.chunk", "created": 1,
                   "model": "document-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"id": "qualification-pi", "object": "chat.completion.chunk", "created": 1,
                   "model": "document-model", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                  {"id": "qualification-pi", "object": "chat.completion.chunk", "created": 1,
                   "model": "document-model", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}]
        stream = Fragments(b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks) + b"data: [DONE]\n\n")
        self.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream", "x-litellm-model-id": "deployment-a",
            "x-litellm-model-api-base": "https://provider.example/v1", "x-litellm-version": "1.100.3"}, stream=stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_tools", [False, True])
async def test_real_pi_qualification_requires_owned_artifact_and_actual_tool_execution(qualification_schema, tmp_path, fake_tools):
    dsn, schema = qualification_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    root = Path(__file__).resolve().parents[2]
    provider = WorkspaceProvider(fake_tools=fake_tools)
    transport = httpx.MockTransport(provider)
    catalog = ModelCatalog(base_url="https://gateway.example", api_key="synthetic-gateway-key",
                           default_model="document-model", transport=transport)
    await catalog.list_models(include_fallback=False)
    conversations = PersistentConversationStore(database)
    admin = AdminStore(database)
    admin.grant("operator", "platform_admin", actor="test-bootstrap", reason="Synthetic qualification")
    profile = ModelRuntimeProfile(alias="document-model", revision=1, supports_tools=True,
        supports_tool_streaming=True, max_output_tokens=128,
        wire_policy=ModelWirePolicy(stream_usage_mode="supported"))
    draft = ModelDraft(expected_revision=0, reason="Synthetic qualification", runtime_profile=profile,
                       allowed_user_ids=["qualification-account"])
    with admin.db:
        admin.db.execute("INSERT INTO admin_models (alias,revision,state,draft_json) VALUES (?,?,?,?)",
                         (profile.alias, 1, "draft", draft.model_dump_json()))
    policy = ModelPolicy(admin, catalog, managed=True)
    runner = PiRpcRunner(executable=str(root / "pi/node_modules/.bin/pi"), extension=str(root / "pi/extension.js"),
                         session_dir=str(tmp_path / "pi"), model_gateway_base_url=base_url + "/internal/model",
                         model_gateway_model="document-model", timeout_seconds=20)
    agent = AgentService(conversations, runner, base_url, 0, 0, af3_executor="disabled")
    owners = WorkspaceSandboxStore(database)
    workspace_provider = FilesystemWorkspaceProvider(tmp_path / "workspace", owner="qualification-account", store=owners)
    files = WorkspaceFiles(workspace_provider)
    artifacts = SandboxArtifactStore(PostgresStatements(database))
    transfer = WorkspaceTransfer(None, workspace_provider, artifacts, conversations=conversations)
    access = WorkspaceAccessPolicy(enabled=True, commands_enabled=False,
                                   user_allowlist_json='["qualification-account"]', capability_hash="")
    agent.workspace_sandbox_provider, agent.workspace_sandbox_store = workspace_provider, owners
    agent.workspace_access, agent.workspace_attempts, agent.workspace_transfer = access, WorkspaceAttemptStore(database), transfer
    app = FastAPI()
    app.state.agent_service, app.state.conversations = agent, conversations
    app.state.workspace_access, app.state.workspace_attempts = access, agent.workspace_attempts
    app.state.workspace_sandbox_store, app.state.workspace_sandbox_provider = owners, workspace_provider
    app.state.workspace_files = files
    app.include_router(workspace_router)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            driver = ModelQualificationDriver(catalog, policy, conversations, agent,
                qualification_user_id="qualification-account", enabled=True, pi_version="0.87.1",
                gateway_version="1.100.3", http_client=client, offline_workspace=True, max_total_tokens=128_000)
            @app.post("/internal/model/v1/chat/completions")
            async def proxy(request: Request, authorization: str | None = Header(default=None)):
                result = await driver.proxy(request, authorization=authorization)
                assert result is not None
                return result
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
            if fake_tools:
                with pytest.raises(ValueError, match="WORKSPACE_INVALID"):
                    await driver.evaluate(profile, actor_id="operator", deployment_fingerprint=catalog.deployment_fingerprint(profile.alias))
                assert artifacts.list("qualification-account") == []
            else:
                evidence = await driver.evaluate(profile, actor_id="operator", deployment_fingerprint=catalog.deployment_fingerprint(profile.alias))
                assert evidence.verification_scope == "offline"
                assert next(case for case in evidence.cases if case.case_id == "workspace_artifact").status == "passed"
                assert not profile.model_copy(update={"qualification_status": evidence.status,
                    "qualification_evidence_id": evidence.evidence_id, "qualification_evidence": evidence}).agent_ready
                assert len(provider.pi_requests) == 3
                owned = artifacts.list("qualification-account")
                assert len(owned) == 1 and owned[0].size == len(b"qualification") and owned[0].available
                assert artifacts.read("qualification-account", owned[0].id) == ("qualification.txt", b"qualification")
                assert artifacts.read("operator", owned[0].id) is None
                attempts = conversations.db.execute("SELECT status FROM workspace_attempts").fetchall()
                assert attempts == [("completed",)]
                workspace_run = conversations.db.execute("SELECT run_id FROM workspace_attempts").fetchone()[0]
                events = conversations.events_for("qualification-account", workspace_run, None)
                created = [event for event in events if isinstance(event, ArtifactCreatedEvent)]
                assert len(created) == 1
                assert created[0].data.artifact_id == owned[0].id
                assert created[0].data.name == "qualification.txt"
                assert created[0].data.kind == owned[0].kind
                assert conversations.events_for("operator", workspace_run, None) is None
                model_attempts = conversations.db.execute("SELECT COUNT(*) FROM agent_token_entries "
                                                        "WHERE kind='model_attempt'").fetchone()[0]
                assert model_attempts == len(provider.requests) + len(provider.pi_requests)
                assert "synthetic-gateway-key" not in "\n".join(path.read_text() for path in (tmp_path / "pi").rglob("*.jsonl"))
                assert driver._active_pi == {}
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        database.close()
