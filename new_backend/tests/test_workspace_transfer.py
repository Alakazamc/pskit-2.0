"""Owned uploads and scoped output transfer over the bridge HTTP consumer boundary."""

import asyncio
import hashlib

import httpx
import pytest

from app.domain.catalog import CatalogStore, ContextNotFound
from app.sandbox_bridge import create_bridge_app
from app.services.workspace_transfer import LocalWorkspace, WorkspaceTransfer


class BridgeClient:
    def __init__(self, app):
        self.app = app

    async def workspace_request(self, user, method, path, **kwargs):
        kwargs.pop("continuation_attempt_id", None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://sandbox",
            headers={"Authorization": "Bearer secret"},
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()


class ArtifactDouble:
    def __init__(self):
        self.records = {}

    def put(self, user, session, attempt, name, raw):
        from app.contracts.catalog import ArtifactRef

        digest = hashlib.sha256(raw).hexdigest()
        key = (user, session, attempt, name, digest)
        if key not in self.records:
            self.records[key] = ArtifactRef(
                id="artifact-" + digest,
                name=name,
                kind="file",
                available=True,
                size=len(raw),
                sha256=digest,
            )
        return self.records[key]


@pytest.mark.asyncio
async def test_owned_upload_is_readable_in_session_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path))
    catalog = CatalogStore()
    upload = catalog.add_uploaded_file("alice", "sequence.fasta", b">A\nACDE")
    bridge = create_bridge_app("alice", "secret")
    transfer = WorkspaceTransfer(catalog, BridgeClient(bridge), ArtifactDouble())
    refs = await transfer.prepare("alice", "one", [upload.id])
    assert (tmp_path / "one" / refs[0].relative_path).read_bytes() == b">A\nACDE"
    assert refs[0].sha256 == hashlib.sha256(b">A\nACDE").hexdigest()
    with pytest.raises(ContextNotFound):
        await transfer.prepare("bob", "one", [upload.id])
    assert not (tmp_path / "two").exists()


@pytest.mark.parametrize("path", ["../escape", "/escape", "files/../escape", "files/a/b"])
def test_workspace_transfer_rejects_escape(tmp_path, path):
    workspace = LocalWorkspace(tmp_path)
    with pytest.raises(ValueError):
        workspace.put("one", path, b"content", hashlib.sha256(b"content").hexdigest())
    assert not (tmp_path / "escape").exists()


def test_workspace_transfer_rejects_symlink_and_oversize_without_overwrite(tmp_path):
    workspace = LocalWorkspace(tmp_path, max_bytes=8)
    (tmp_path / "one/files").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    (tmp_path / "one/files/link").symlink_to(outside)
    with pytest.raises(ValueError):
        workspace.put(
            "one", "files/link", b"replacement", hashlib.sha256(b"replacement").hexdigest()
        )
    with pytest.raises(ValueError):
        workspace.put("one", "files/link", b"small", hashlib.sha256(b"small").hexdigest())
    assert outside.read_bytes() == b"original"
    (tmp_path / "two").symlink_to(tmp_path / "one", target_is_directory=True)
    with pytest.raises(ValueError):
        workspace.put("two", "files/new", b"small", hashlib.sha256(b"small").hexdigest())


@pytest.mark.asyncio
async def test_output_roundtrip_preserves_artifact_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path))
    output = tmp_path / "one/artifacts/attempt"
    output.mkdir(parents=True)
    (output / "result.txt").write_bytes(b"result")
    artifacts = ArtifactDouble()
    transfer = WorkspaceTransfer(
        CatalogStore(), BridgeClient(create_bridge_app("alice", "secret")), artifacts
    )
    first = await transfer.collect("alice", "one", "attempt")
    second = await transfer.collect("alice", "one", "attempt")
    assert first == second
    assert len(artifacts.records) == 1
    assert next(iter(artifacts.records))[0] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["sweep", "replace"])
async def test_active_workspace_transfer_survives_lifecycle(lifecycle, tmp_path, monkeypatch):
    from test_sandbox_lifecycle import AUTH, DockerDouble, settings

    from app.adapters.live.sandbox_pi import SandboxPiRunner
    from app.domain.sandboxes import MemorySandboxActivityStore
    from app.sandbox_manager import create_manager_app

    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path))
    clock = [0.0]
    entered, proceed = asyncio.Event(), asyncio.Event()
    import hmac

    derived = hmac.new(b"derived-token-secret", b"alice", hashlib.sha256).hexdigest()
    bridge = create_bridge_app("alice", derived)

    class SlowBridge(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.path == "/v1/workspace/files":
                entered.set()
                await proceed.wait()
            return await httpx.ASGITransport(app=bridge).handle_async_request(request)

    docker = DockerDouble()
    manager = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(clock=lambda: clock[0]),
        clock=lambda: clock[0],
        docker_transport=httpx.MockTransport(docker),
        bridge_transport=SlowBridge(),
    )
    runner = SandboxPiRunner(
        manager_url="http://manager",
        manager_token="manager-secret-123456",
        model="m",
        manager_transport=httpx.ASGITransport(app=manager),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=manager), base_url="http://manager", headers=AUTH
    ) as client:
        await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        clock[0] = 1801
        import base64

        transfer = asyncio.create_task(
            runner.workspace_request(
                "alice",
                "POST",
                "/v1/workspace/files",
                json={
                    "session_id": "one",
                    "relative_path": "files/input.txt",
                    "sha256": hashlib.sha256(b"input").hexdigest(),
                    "content_b64": base64.b64encode(b"input").decode(),
                },
            )
        )
        await entered.wait()
        try:
            if lifecycle == "sweep":
                assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 0}
            else:
                summary = (await client.get("/v1/sandboxes")).json()[0]
                await client.post(
                    "/v1/sandboxes/alice/drain", json={"expected_revision": summary["revision"]}
                )
                response = await client.post(
                    "/v1/sandboxes/alice/replace",
                    json={"image_digest": "pskit-agent@sha256:" + "a" * 64},
                )
                assert response.json()["state"] == "waiting"
        finally:
            proceed.set()
            await transfer
        assert (tmp_path / "one/files/input.txt").read_bytes() == b"input"
        assert (await client.get("/v1/sandboxes")).json()[0]["active_sessions"] == []


@pytest.mark.asyncio
async def test_admitted_pi_turn_collects_artifacts_after_drain(tmp_path, monkeypatch):
    import hmac
    from pathlib import Path

    from test_sandbox_lifecycle import AUTH, DockerDouble, settings

    from app.adapters.live.sandbox_pi import SandboxPiRunner
    from app.domain.sandboxes import MemorySandboxActivityStore
    from app.sandbox_manager import create_manager_app

    monkeypatch.setenv("RESEARCH_AGENT_PI_SESSION_DIR", str(tmp_path))
    entered, proceed = asyncio.Event(), asyncio.Event()

    class Pi:
        async def prompt(self, session, message, event, **kwargs):
            directory = Path(kwargs["environment"]["PSKIT_ARTIFACT_DIR"])
            (directory / "result.txt").write_bytes(b"result")
            entered.set()
            await proceed.wait()
            return {"text": "done", "session_file": str(tmp_path / session / ".pi/turn.jsonl")}

    derived = hmac.new(b"derived-token-secret", b"alice", hashlib.sha256).hexdigest()
    manager = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(DockerDouble()),
        bridge_transport=httpx.ASGITransport(
            app=create_bridge_app("alice", derived, lambda _: Pi())
        ),
    )
    runner = SandboxPiRunner(
        manager_url="http://manager",
        manager_token="manager-secret-123456",
        model="m",
        manager_transport=httpx.ASGITransport(app=manager),
    )
    transfer = WorkspaceTransfer(CatalogStore(), runner, ArtifactDouble())
    collected = []

    async def collect(result):
        collected.extend(await transfer.collect("alice", "one", result["attempt_id"]))

    task = asyncio.create_task(
        runner.prompt(
            "one",
            "hi",
            lambda _: None,
            environment={"PSKIT_USER_ID": "alice"},
            include_attempt_id=True,
            after_attempt=collect,
        )
    )
    await entered.wait()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=manager), base_url="http://manager", headers=AUTH
    ) as client:
        summary = (await client.get("/v1/sandboxes")).json()[0]
        await client.post(
            "/v1/sandboxes/alice/drain", json={"expected_revision": summary["revision"]}
        )
        proceed.set()
        result = await task
        assert result["text"] == "done"
        assert len(collected) == 1
        assert (await client.get("/v1/sandboxes")).json()[0]["active_sessions"] == []
