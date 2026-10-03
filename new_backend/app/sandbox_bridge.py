"""Private streaming bridge that runs Pi inside one user's sandbox container."""

import asyncio
import base64
import binascii
import json
import os
import secrets
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.adapters.live.pi_rpc import PiRpcError, PiRpcRunner
from app.domain.catalog import MAX_IMAGE_FILE_BYTES, image_mime_type


class SandboxPrompt(BaseModel):
    """One Run-scoped Pi turn sent by the trusted Python backend."""

    user_id: str
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    message: str
    model: str = Field(min_length=1)
    session_file: str | None = None
    legacy_session_b64: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    allow_handled: bool = False
    system_prompt_suffix: str = ""
    images: list[dict[str, str]] = Field(default_factory=list, max_length=2)


def _default_runner(model: str) -> PiRpcRunner:
    """Construct Pi using only this container's filesystem and private backend URL."""
    root = Path(__file__).resolve().parents[1]
    internal_url = os.environ.get("RESEARCH_AGENT_INTERNAL_API_URL", "http://backend:8000")
    return PiRpcRunner(
        session_dir=os.environ.get("RESEARCH_AGENT_PI_SESSION_DIR", "/workspace/sessions"),
        extension=str(root / "pi" / "extension.js"),
        model_gateway_base_url=f"{internal_url.rstrip('/')}/internal/model",
        model_gateway_model=model,
        system_prompt=(root / "pi" / "system-prompt.md").read_text(),
    )


def create_bridge_app(
    owner_id: str, token: str,
    runner_factory: Callable[[str], Any] = _default_runner,
) -> FastAPI:
    """Create one private HTTP endpoint bound to a single sandbox owner."""
    if not owner_id or not token:
        raise ValueError("Sandbox owner and bridge token are required")
    app = FastAPI(title="PSKit Sandbox Bridge", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/pi/prompt")
    async def prompt(
        request: SandboxPrompt, authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        if not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        if request.user_id != owner_id:
            raise HTTPException(status_code=403, detail="Sandbox owner mismatch")
        for image in request.images:
            mime, encoded = image.get("mimeType"), image.get("data")
            if image.get("type") != "image" or not isinstance(encoded, str) or not isinstance(mime, str):
                raise HTTPException(status_code=422, detail="Invalid image")
            if len(encoded) > MAX_IMAGE_FILE_BYTES * 4 // 3 + 8:
                raise HTTPException(status_code=413, detail="Image exceeds size limit")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except binascii.Error as exc:
                raise HTTPException(status_code=422, detail="Invalid image") from exc
            suffix = {"image/png": ".png", "image/jpeg": ".jpg",
                      "image/gif": ".gif", "image/webp": ".webp"}.get(mime, "")
            if len(raw) > MAX_IMAGE_FILE_BYTES or image_mime_type("image" + suffix, raw) != mime:
                raise HTTPException(status_code=422, detail="Invalid image")
        if request.session_file is not None:
            session_root = (Path(os.environ.get(
                "RESEARCH_AGENT_PI_SESSION_DIR", "/workspace/sessions",
            )) / request.session_id).resolve()
            if not Path(request.session_file).resolve().is_relative_to(session_root):
                raise HTTPException(status_code=422, detail="Session file outside workspace")
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
            session_root = (Path(os.environ.get(
                "RESEARCH_AGENT_PI_SESSION_DIR", "/workspace/sessions",
            )) / request.session_id).resolve()
            session_root.mkdir(parents=True, exist_ok=True)
            imported = session_root / f"import-{uuid.uuid4().hex}.jsonl"
            imported.write_bytes(content)
            imported.chmod(0o600)

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)

        async def relay(event: dict[str, Any]) -> None:
            await queue.put({"kind": "event", "data": event})

        async def execute() -> None:
            try:
                result = await runner_factory(request.model).prompt(
                    request.session_id, request.message, relay,
                    session_file=str(imported) if imported else request.session_file,
                    environment=request.environment,
                    allow_handled=request.allow_handled,
                    system_prompt_suffix=request.system_prompt_suffix,
                    **({"images": request.images} if request.images else {}),
                )
                await queue.put({"kind": "result", "data": result})
            except PiRpcError as exc:
                await queue.put({"kind": "error", "code": exc.code,
                                 "message": str(exc), "retryable": exc.retryable})
            except Exception:  # noqa: BLE001 - Do not expose internal exception details.
                await queue.put({"kind": "error", "code": "PI_RUN_FAILED",
                                 "message": "Sandbox Pi failed", "retryable": False})
            finally:
                if imported is not None:
                    imported.unlink(missing_ok=True)
                await queue.put(None)

        task = asyncio.create_task(execute())

        async def records():
            try:
                while (record := await queue.get()) is not None:
                    yield json.dumps(record, ensure_ascii=False) + "\n"
            finally:
                if not task.done():
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
        os.environ["PSKIT_SANDBOX_USER_ID"], os.environ["PSKIT_SANDBOX_BRIDGE_TOKEN"],
    )


app = None  # Uvicorn uses ``create_app`` with its factory flag.
