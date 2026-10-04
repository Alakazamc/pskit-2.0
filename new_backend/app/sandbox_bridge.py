"""Private streaming bridge that runs Pi inside one user's sandbox container."""

import asyncio
import base64
import binascii
import json
import os
import secrets
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.adapters.live.pi_rpc import PiRpcError, PiRpcRunner
from app.contracts.sandbox import Identity, SandboxPromptRequest
from app.domain.catalog import MAX_IMAGE_FILE_BYTES, image_mime_type
from app.domain.sandboxes import SandboxConflict
from app.services.sandbox_sessions import SandboxSessionCoordinator
from app.services.workspace_transfer import MAX_WORKSPACE_FILE_BYTES, LocalWorkspace

TransferAttemptHeader = Annotated[Identity | None, Header(alias="X-PSKit-Transfer-Attempt")]


class SandboxPrompt(SandboxPromptRequest):
    # Compatibility for old trusted backends; updated runners always bind this ID.
    attempt_id: str = Field(
        default_factory=lambda: "attempt-" + uuid.uuid4().hex, pattern=r"^[A-Za-z0-9_-]{1,128}$"
    )


class WorkspaceUpload(BaseModel):
    session_id: Identity
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_b64: str = Field(max_length=MAX_WORKSPACE_FILE_BYTES * 4 // 3 + 8)


def _default_runner(model: str) -> PiRpcRunner:
    """Construct Pi using only this container's filesystem and private backend URL."""
    root = Path(__file__).resolve().parents[1]
    internal_url = os.environ.get("RESEARCH_AGENT_INTERNAL_API_URL", "http://sandbox-gateway:8080")
    return PiRpcRunner(
        session_dir=os.environ.get("RESEARCH_AGENT_PI_SESSION_DIR", "/workspace/sessions"),
        extension=str(root / "pi" / "extension.js"),
        model_gateway_base_url=f"{internal_url.rstrip('/')}/internal/model",
        model_gateway_model=model,
        system_prompt=(root / "pi" / "system-prompt.md").read_text(),
    )


def create_bridge_app(
    owner_id: str,
    token: str,
    runner_factory: Callable[[str], Any] = _default_runner,
) -> FastAPI:
    """Create one private HTTP endpoint bound to a single sandbox owner."""
    if not owner_id or not token:
        raise ValueError("Sandbox owner and bridge token are required")
    app = FastAPI(title="PSKit Sandbox Bridge", docs_url=None, redoc_url=None, openapi_url=None)
    session_root = Path(os.environ.get("RESEARCH_AGENT_PI_SESSION_DIR", "/workspace/sessions"))
    coordinator = SandboxSessionCoordinator(session_root, runner_factory)
    boot_id = uuid.uuid4().hex

    def authorize(authorization):
        if not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.exception_handler(SandboxConflict)
    async def conflict(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/v1/pi/activity")
    async def activity(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {"boot_id": boot_id, "attempts": coordinator.activity()}

    @app.get("/v1/pi/attempts/{attempt_id}")
    async def status(attempt_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return coordinator.status(attempt_id)

    @app.post("/v1/pi/attempts/{attempt_id}/cancel")
    async def cancel(attempt_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return await coordinator.cancel(attempt_id)

    workspace = LocalWorkspace(session_root)

    def transferred_response(data, attempt_id):
        if attempt_id is None:
            return data

        class TransferResponse(StreamingResponse):
            async def __call__(self, scope, receive, send):
                state = "completed"
                try:
                    await super().__call__(scope, receive, send)
                except asyncio.CancelledError:
                    state = "cancelled"
                    raise
                except BaseException:
                    state = "failed"
                    raise
                finally:
                    coordinator.finish_transfer(attempt_id, state)

        async def body():
            yield json.dumps(data).encode()

        return TransferResponse(body(), media_type="application/json")

    @app.post("/v1/workspace/files")
    async def upload(
        payload: WorkspaceUpload,
        authorization: str | None = Header(default=None),
        transfer_attempt: TransferAttemptHeader = None,
    ):
        authorize(authorization)
        if transfer_attempt:
            coordinator.start_transfer(payload.session_id, transfer_attempt)
        try:
            raw = base64.b64decode(payload.content_b64, validate=True)
            workspace.put(payload.session_id, payload.relative_path, raw, payload.sha256)
        except (ValueError, OSError) as exc:
            if transfer_attempt:
                coordinator.finish_transfer(transfer_attempt, "failed")
            raise HTTPException(status_code=422, detail="Invalid workspace file") from exc
        return transferred_response({"sha256": payload.sha256, "size": len(raw)}, transfer_attempt)

    @app.get("/v1/workspace/artifacts")
    async def artifacts(
        session_id: Identity,
        attempt_id: Identity,
        authorization: str | None = Header(default=None),
        transfer_attempt: TransferAttemptHeader = None,
    ):
        authorize(authorization)
        if transfer_attempt:
            coordinator.start_transfer(session_id, transfer_attempt)
        try:
            data = {"files": workspace.export(session_id, attempt_id)}
        except (ValueError, OSError) as exc:
            if transfer_attempt:
                coordinator.finish_transfer(transfer_attempt, "failed")
            raise HTTPException(status_code=422, detail="Invalid workspace output") from exc
        return transferred_response(data, transfer_attempt)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/pi/prompt")
    async def prompt(
        request: SandboxPrompt,
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        if not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        if request.user_id != owner_id:
            raise HTTPException(status_code=403, detail="Sandbox owner mismatch")
        for image in request.images:
            mime, encoded = image.get("mimeType"), image.get("data")
            if (
                image.get("type") != "image"
                or not isinstance(encoded, str)
                or not isinstance(mime, str)
            ):
                raise HTTPException(status_code=422, detail="Invalid image")
            if len(encoded) > MAX_IMAGE_FILE_BYTES * 4 // 3 + 8:
                raise HTTPException(status_code=413, detail="Image exceeds size limit")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except binascii.Error as exc:
                raise HTTPException(status_code=422, detail="Invalid image") from exc
            suffix = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }.get(mime, "")
            if len(raw) > MAX_IMAGE_FILE_BYTES or image_mime_type("image" + suffix, raw) != mime:
                raise HTTPException(status_code=422, detail="Invalid image")
        try:
            with workspace.directory([request.session_id], create=True):
                pass
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid session workspace") from exc
        if request.session_file is not None:
            session_directory = session_root / request.session_id
            committed = Path(request.session_file)
            try:
                relative = committed.relative_to(session_directory)
                parts = workspace._parts(relative.as_posix())
                with workspace.directory([request.session_id, *parts[:-1]]) as descriptor:
                    info = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Transcript must be a regular file")
            except (ValueError, OSError) as exc:
                raise HTTPException(
                    status_code=422, detail="Session file outside workspace"
                ) from exc
        if request.session_file and request.legacy_session_b64:
            raise HTTPException(status_code=422, detail="Only one session source is allowed")
        imported: Path | None = None
        if request.legacy_session_b64 is not None:
            try:
                content = base64.b64decode(request.legacy_session_b64, validate=True)
            except binascii.Error as exc:
                raise HTTPException(status_code=422, detail="Invalid session import") from exc
            if len(content) > 32 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Session import exceeds 32 MiB")
            name = f"import-{uuid.uuid4().hex}.jsonl"
            with workspace.directory([request.session_id, ".pi"], create=True) as descriptor:
                handle = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=descriptor,
                )
                with os.fdopen(handle, "wb") as stream:
                    stream.write(content)
            imported = session_root / request.session_id / ".pi" / name

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)

        async def relay(event: dict[str, Any]) -> None:
            await queue.put({"kind": "event", "data": event})

        async def execute() -> None:
            try:
                coordinated = request.model_copy(
                    update={
                        "session_file": str(imported) if imported else request.session_file,
                    }
                )
                result = await coordinator.prompt(coordinated, relay)
                await queue.put({"kind": "result", "data": result})
            except PiRpcError as exc:
                await queue.put(
                    {
                        "kind": "error",
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                    }
                )
            except Exception:  # noqa: BLE001 - Do not expose internal exception details.
                await queue.put(
                    {
                        "kind": "error",
                        "code": "PI_RUN_FAILED",
                        "message": "Sandbox Pi failed",
                        "retryable": False,
                    }
                )
            except asyncio.CancelledError:
                while queue.qsize() > 126:
                    queue.get_nowait()
                queue.put_nowait(
                    {
                        "kind": "error",
                        "code": "PI_CANCELLED",
                        "message": "Sandbox Pi cancelled",
                        "retryable": False,
                    }
                )
            finally:
                if imported is not None:
                    imported.unlink(missing_ok=True)
                if asyncio.current_task().cancelling():
                    queue.put_nowait(None)
                else:
                    await queue.put(None)

        task = asyncio.create_task(execute())

        async def records():
            try:
                while (record := await queue.get()) is not None:
                    yield json.dumps(record, ensure_ascii=False) + "\n"
            finally:
                if not task.done() and not task.cancelling():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return StreamingResponse(records(), media_type="application/x-ndjson")

    return app


def create_app() -> FastAPI:
    """Load the immutable owner and private token injected by the sandbox manager."""
    return create_bridge_app(
        os.environ["PSKIT_SANDBOX_USER_ID"],
        os.environ["PSKIT_SANDBOX_BRIDGE_TOKEN"],
    )


app = None  # Uvicorn uses ``create_app`` with its factory flag.
